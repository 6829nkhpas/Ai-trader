import jwt from 'jsonwebtoken';
import crypto from 'crypto';
import { UserRepository } from '../repositories/user.repository';
import { BrokerRepository } from '../repositories/broker.repository';
import { redis } from '../config/redis';
import { prisma } from '../db';
import { KiteService } from './kiteService';

const userRepository = new UserRepository();
const brokerRepository = new BrokerRepository();

const JWT_SECRET = process.env.JWT_SECRET || 'alpha-jwt-secret-key-39281!@';
const PROFILE_CACHE_TTL = 300; // 5 minutes in seconds

export class AuthService {
  // Signs and returns JWT
  generateToken(userId: string, tier: string): string {
    return jwt.sign({ userId, tier }, JWT_SECRET, { expiresIn: '24h' });
  }

  isTrialActive(createdAt: Date | string): boolean {
    const createdTime = new Date(createdAt).getTime();
    return (Date.now() - createdTime) < 3 * 24 * 60 * 60 * 1000;
  }

  getEffectiveTier(user: any): string {
    if (process.env.NODE_ENV !== 'production') {
      return 'PREMIUM';
    }
    if (user.tier === 'FREE' && this.isTrialActive(user.createdAt)) {
      return 'PRO';
    }
    return user.tier;
  }

  // 1. Core Login/Auto-Registration Business Logic
  async authenticateUser(email: string, password: string) {
    let user = await userRepository.findByEmail(email);

    if (!user) {
      console.log(`[Auth Service] User ${email} not found. Creating automatic record (FREE).`);
      user = await userRepository.createUser({ email, password, tier: 'FREE' });
    } else {
      if (user.password !== password) {
        throw new Error('Invalid credentials');
      }
    }

    if (!user) {
      throw new Error('User authentication failed to initialize');
    }

    const effectiveTier = this.getEffectiveTier(user);
    const token = this.generateToken(user.id, effectiveTier);

    return {
      token,
      user: {
        id: user.id,
        email: user.email,
        name: user.name,
        tier: effectiveTier,
        walletBalance: user.walletBalance,
        brokerConnection: (user as any).brokerConnection || (process.env.MOCK_BROKER === 'true' ? {
          id: 'mock-broker-conn-id',
          broker: 'ZERODHA',
          brokerUserId: 'DEV123',
          userName: 'Mock Developer Account',
          userShortname: 'MockDev',
          apiKey: 'mock_api_key',
          accessToken: 'mock_access_token',
          avatarUrl: null
        } : null)
      }
    };
  }

  // Dedicated User Registration Logic
  async registerUser(email: string, password: string, name?: string, tier: string = 'FREE') {
    const existingUser = await userRepository.findByEmail(email);
    if (existingUser) {
      throw new Error('User already exists');
    }

    console.log(`[Auth Service] Creating new user profile: ${email} (${tier})`);
    const user = await userRepository.createUser({ email, password, name, tier });

    const effectiveTier = this.getEffectiveTier(user);
    const token = this.generateToken(user.id, effectiveTier);

    return {
      token,
      user: {
        id: user.id,
        email: user.email,
        name: user.name,
        tier: effectiveTier,
        walletBalance: user.walletBalance,
        brokerConnection: (user as any).brokerConnection || (process.env.MOCK_BROKER === 'true' ? {
          id: 'mock-broker-conn-id',
          broker: 'ZERODHA',
          brokerUserId: 'DEV123',
          userName: 'Mock Developer Account',
          userShortname: 'MockDev',
          apiKey: 'mock_api_key',
          accessToken: 'mock_access_token',
          avatarUrl: null
        } : null)
      }
    };
  }

  // 2. Broker Kite OAuth Redirection Business Logic
  async getBrokerConnectUrl(userId?: string) {
    let targetUserId = userId;

    if (!targetUserId) {
      const firstUser = await userRepository.findFirstUser();
      if (!firstUser) {
        throw new Error('No user profile available and no userId query parameter provided.');
      }
      targetUserId = firstUser.id;
    }

    const apiKey = process.env.KITE_API_KEY || 'sn5szo9fhwkjdi8a';
    const callbackUrl = `https://kite.trade/connect/login?api_key=${apiKey}&v=3&redirect_params=state%3D${targetUserId}`;

    console.log(`[Auth Service] Connect URL generated: ${callbackUrl}`);

    return {
      userId: targetUserId,
      callbackUrl
    };
  }

  // 3. Save Broker credentials via real Zerodha token exchange
  async saveBrokerAccessToken(userId: string | undefined, requestToken: string) {
    let targetUserId: string;
    if (!userId) {
      console.log('[Auth Service] userId missing from Zerodha callback state. Falling back to first user...');
      const firstUser = await userRepository.findFirstUser();
      if (!firstUser) {
        throw new Error('No user profile available to link Zerodha account');
      }
      targetUserId = firstUser.id;
    } else {
      targetUserId = userId;
    }

    const user = await userRepository.findById(targetUserId);
    if (!user) {
      throw new Error('User associated with broker authorization session not found');
    }

    const apiKey = process.env.KITE_API_KEY || 'sn5szo9fhwkjdi8a';
    const apiSecret = process.env.KITE_API_SECRET || 'lic12bvwjz1d89tkepbk2cbsxfwfbofn';

    let kiteData;

    // Generate real SHA-256 checksum: SHA256(api_key + request_token + api_secret)
    const checksum = crypto
      .createHash('sha256')
      .update(apiKey + requestToken + apiSecret)
      .digest('hex');

    console.log(`[Auth Service] Exchanging Zerodha request token for access token (API Key: ${apiKey})...`);

    // Perform real Zerodha Kite Connect v3 OAuth token exchange HTTP request
    const response = await fetch('https://api.kite.trade/session/token', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        'x-kite-version': '3'
      },
      body: new URLSearchParams({
        api_key: apiKey,
        request_token: requestToken,
        checksum: checksum
      }).toString()
    });

    if (!response.ok) {
      const errText = await response.text();
      console.error('[Auth Service] Zerodha token exchange failed:', errText);
      throw new Error(`Zerodha OAuth exchange failed: ${errText || response.statusText}`);
    }

    const result = (await response.json()) as any;
    console.log('[Auth Service] Zerodha OAuth raw response payload:', JSON.stringify(result, null, 2));
    if (result.status !== 'success' || !result.data) {
      throw new Error(`Zerodha OAuth returned unsuccessful status: ${result.message || 'Unknown error'}`);
    }

    kiteData = result.data;

    // Map Zerodha's exact response structure directly to our schema properties
    const brokerData = {
      accessToken: kiteData.access_token,
      brokerUserId: kiteData.user_id,
      apiKey: kiteData.api_key || apiKey,
      publicToken: kiteData.public_token,
      refreshToken: kiteData.refresh_token,
      userType: kiteData.user_type,
      email: kiteData.email || user.email,
      userName: kiteData.user_name || user.name || 'John Doe',
      userShortname: kiteData.user_shortname || user.name?.split(' ')[0] || 'John',
      broker: kiteData.broker || 'ZERODHA',
      avatarUrl: kiteData.avatar_url,
      loginTime: kiteData.login_time,
      exchanges: kiteData.exchanges || [],
      products: kiteData.products || [],
      orderTypes: kiteData.order_types || []
    };

    await brokerRepository.upsertBrokerConnection(targetUserId, brokerData);

    // Update User profile name to Zerodha account holder's name
    try {
      await prisma.user.update({
        where: { id: targetUserId },
        data: { name: brokerData.userName }
      });
      console.log(`[Auth Service] Updated User ${targetUserId} name to Zerodha userName: ${brokerData.userName}`);
    } catch (err: any) {
      console.warn(`[Auth Service] Failed to update User name in database:`, err.message);
    }

    // Synchronize to root .env file for local ingestion and aggregator services to reference
    try {
      const fs = require('fs');
      const path = require('path');
      const envPath = path.resolve(__dirname, '../../../../.env');
      if (fs.existsSync(envPath)) {
        let envContent = fs.readFileSync(envPath, 'utf8');

        // Update KITE_ACCESS_TOKEN
        if (envContent.includes('KITE_ACCESS_TOKEN=')) {
          envContent = envContent.replace(/KITE_ACCESS_TOKEN=.*/g, `KITE_ACCESS_TOKEN=${brokerData.accessToken}`);
        } else {
          envContent += `\nKITE_ACCESS_TOKEN=${brokerData.accessToken}`;
        }

        // Update KITE_REQUEST_TOKEN
        if (envContent.includes('KITE_REQUEST_TOKEN=')) {
          envContent = envContent.replace(/KITE_REQUEST_TOKEN=.*/g, `KITE_REQUEST_TOKEN=${requestToken}`);
        } else {
          envContent += `\nKITE_REQUEST_TOKEN=${requestToken}`;
        }

        fs.writeFileSync(envPath, envContent, 'utf8');
        console.log(`[Auth Service] Synchronized KITE_ACCESS_TOKEN and KITE_REQUEST_TOKEN to root .env file.`);
      }
    } catch (e: any) {
      console.warn(`[Auth Service] Failed to synchronize tokens to root .env file:`, e.message);
    }

    // Invalidate Redis profile cache so that the new broker connection is returned immediately
    const cacheKey = `user:profile:${targetUserId}`;
    try {
      await redis.del(cacheKey);
      console.log(`[Auth Service Caching] Invalidated Redis Cache for user: ${targetUserId} due to new broker connection.`);
    } catch (err: any) {
      console.warn(`[Auth Service Caching] Redis del operation failed:`, err.message);
    }

    return {
      userId: targetUserId,
      ...brokerData
    };
  }

  // 4. Upgrade User Subscription Tier & Invalidate Redis Cache
  async upgradeUserTier(userId: string, tier: string) {
    const user = await userRepository.findById(userId);
    if (!user) {
      throw new Error('User profile not found');
    }

    const updatedUser = await userRepository.updateTier(userId, tier);

    // Synchronize direct subscription record details inside shared Postgres table
    if (tier === 'PRO' || tier === 'PREMIUM') {
      const currentPeriodEnd = new Date();
      currentPeriodEnd.setDate(currentPeriodEnd.getDate() + 30);
      try {
        await prisma.$executeRaw`
          INSERT INTO "Subscription" (id, user_id, status, current_period_end, stripe_customer_id, "createdAt", "updatedAt")
          VALUES (${crypto.randomUUID()}, ${userId}, 'ACTIVE', ${currentPeriodEnd}, 'phonepe_merchant_cust', NOW(), NOW())
          ON CONFLICT (user_id) DO UPDATE SET 
            status = 'ACTIVE', 
            current_period_end = ${currentPeriodEnd}, 
            "updatedAt" = NOW()
        `;
        console.log(`[Auth Service] Activated premium database subscription record for user: ${userId}`);
      } catch (err: any) {
        console.warn(`[Auth Service] Failed to activate premium subscription:`, err.message);
      }
    } else {
      try {
        await prisma.$executeRaw`
          UPDATE "Subscription" 
          SET status = 'INACTIVE', "updatedAt" = NOW() 
          WHERE user_id = ${userId}
        `;
        console.log(`[Auth Service] Downgraded database subscription to INACTIVE for user: ${userId}`);
      } catch (err: any) {
        console.warn(`[Auth Service] Failed to deactivate premium subscription:`, err.message);
      }
    }

    // CRITICAL REQUIREMENT: Cache Invalidation
    // Clear user cached profile in Redis so the me profile reflects the new status instantly!
    const cacheKey = `user:profile:${userId}`;
    console.log(`[Auth Service Caching] Invalidating Redis Cache key: ${cacheKey}`);
    await redis.del(cacheKey);

    return {
      id: updatedUser.id,
      email: updatedUser.email,
      tier: updatedUser.tier
    };
  }

  // 5. Fetch User Profile with ioredis Caching
  async getUserProfile(userId: string) {
    const cacheKey = `user:profile:${userId}`;

    // Check Redis cache first
    try {
      const cachedProfile = await redis.get(cacheKey);
      if (cachedProfile) {
        console.log(`[Auth Service Caching] Cache Hit! Profile returned from Redis: ${cacheKey}`);
        return JSON.parse(cachedProfile);
      }
    } catch (err: any) {
      console.warn(`[Auth Service Caching] Redis get operation failed:`, err.message);
    }

    console.log(`[Auth Service Caching] Cache Miss. Fetching profile from database...`);
    let user = await userRepository.findById(userId);
    if (!user) {
      if (process.env.MOCK_BROKER === 'true') {
        console.log(`[Auth Service Caching] Mock developer user not found in database. Auto-creating...`);
        user = await userRepository.createUser({
          id: userId,
          email: 'dev@alphasuite.app',
          password: 'dev-password',
          name: 'Mock Developer Account',
          tier: 'PREMIUM'
        });
      } else {
        throw new Error('User not found');
      }
    }

    // Auto-inject mock broker connection for development
    if (process.env.MOCK_BROKER === 'true' && !user.brokerConnection) {
      user.brokerConnection = {
        id: 'mock-broker-conn-id',
        userId: user.id,
        broker: 'ZERODHA',
        brokerUserId: 'DEV123',
        userName: 'Mock Developer Account',
        userShortname: 'MockDev',
        apiKey: 'mock_api_key',
        accessToken: 'mock_access_token',
        avatarUrl: null,
        publicToken: 'mock_public_token',
        refreshToken: 'mock_refresh_token',
        userType: 'individual',
        email: user.email,
        loginTime: new Date(),
        exchanges: ['NSE', 'BSE'],
        products: ['CNC', 'MIS'],
        orderTypes: ['LIMIT', 'MARKET'],
        createdAt: new Date(),
        updatedAt: new Date()
      } as any;
    }

    // Self-healing: if user name is null or default, but broker userName exists, set it
    let displayName = user.name;
    if ((!displayName || displayName === 'John Doe' || displayName === 'Strat AI Client' || displayName === 'Strat AI User') && user.brokerConnection?.userName) {
      displayName = user.brokerConnection.userName;
      try {
        await prisma.user.update({
          where: { id: userId },
          data: { name: displayName }
        });
        console.log(`[Auth Service] Self-healed user name to Zerodha name: ${displayName}`);
      } catch (err: any) {
        console.warn(`[Auth Service] Self-healing user name update failed:`, err.message);
      }
    }

    // Fetch and sync real cash balance from Zerodha margins if broker connected
    let realWalletBalance = user.walletBalance;
    if (user.brokerConnection) {
      try {
        const kiteService = new KiteService();
        const margins = await kiteService.getMargins(
          user.brokerConnection.apiKey || '',
          user.brokerConnection.accessToken || ''
        );
        // Prefer net margin (buying power) if non-zero, fallback to live_balance or cash
        const netMargin = margins?.equity?.net;
        const cashMargin = margins?.equity?.available?.cash;
        const liveBalance = margins?.equity?.available?.live_balance;
        const finalMargin = (typeof netMargin === 'number' && netMargin !== 0)
          ? netMargin
          : (typeof liveBalance === 'number' ? liveBalance : (typeof cashMargin === 'number' ? cashMargin : null));

        if (finalMargin !== null) {
          realWalletBalance = finalMargin;
          // Dynamically persist to database to keep in sync
          await prisma.user.update({
            where: { id: userId },
            data: { walletBalance: finalMargin }
          });
          console.log(`[Auth Service] Dynamic wallet balance synced with real Kite margin cash/net: ₹${finalMargin}`);
        }
      } catch (err: any) {
        console.warn(`[Auth Service] Failed to dynamically sync broker cash margin to walletBalance:`, err.message);
      }
    }

    // Query shared Subscription table directly using Prisma raw query
    let subscription = null;
    if (process.env.NODE_ENV !== 'production') {
      subscription = {
        id: 'dev-subscription-bypass-id',
        userId: userId,
        stripeCustomerId: 'phonepe_merchant_cust',
        razorpayCustomerId: null,
        status: 'ACTIVE',
        currentPeriodEnd: new Date(Date.now() + 50 * 365 * 24 * 60 * 60 * 1000), // 50 years from now
        createdAt: new Date(),
        updatedAt: new Date()
      };
    } else {
      try {
        const subResult: any = await prisma.$queryRaw`SELECT * FROM "Subscription" WHERE "user_id" = ${userId} LIMIT 1`;
        if (subResult && subResult.length > 0) {
          const rawSub = subResult[0];
          subscription = {
            id: rawSub.id,
            userId: rawSub.user_id,
            stripeCustomerId: rawSub.stripe_customer_id,
            razorpayCustomerId: rawSub.razorpay_customer_id,
            status: rawSub.status,
            currentPeriodEnd: rawSub.current_period_end,
            createdAt: rawSub.createdAt,
            updatedAt: rawSub.updatedAt
          };
        }
      } catch (err: any) {
        console.warn(`[Auth Service] Failed to retrieve subscription via raw query:`, err.message);
      }
    }

    const effectiveTier = this.getEffectiveTier(user);
    const trialActive = user.tier === 'FREE' && this.isTrialActive(user.createdAt);
    const trialExpiresAt = new Date(new Date(user.createdAt).getTime() + 3 * 24 * 60 * 60 * 1000);

    const profileData = {
      id: user.id,
      email: user.email,
      name: displayName || user.name || 'Strat AI Client',
      tier: effectiveTier,
      trialActive,
      trialExpiresAt,
      walletBalance: realWalletBalance,
      createdAt: user.createdAt,
      brokerConnection: (user as any).brokerConnection || null,
      subscription: subscription || null
    };

    // Save to Redis cache for 5 minutes
    try {
      await redis.setex(cacheKey, PROFILE_CACHE_TTL, JSON.stringify(profileData));
      console.log(`[Auth Service Caching] Profile cached successfully in Redis: ${cacheKey} (TTL: ${PROFILE_CACHE_TTL}s)`);
    } catch (err: any) {
      console.warn(`[Auth Service Caching] Redis setex operation failed:`, err.message);
    }

    return profileData;
  }
}

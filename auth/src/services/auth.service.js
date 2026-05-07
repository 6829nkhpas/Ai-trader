import { hashPassword, verifyPassword } from '../crypto/hasher.js';
import { config } from '../config.js';
import { PasswordComplexityError, DuplicateEmailError, AuthenticationError } from '../errors/index.js';
import { findUserByEmail, insertUser, insertCredential, getPasswordHash } from '../repository/user.repository.js';

export function validatePasswordComplexity(password) {
  const rules = config.password;

  if (typeof password !== 'string') {
    throw new PasswordComplexityError('Password must be a string.');
  }

  if (password.length < rules.minLength) {
    throw new PasswordComplexityError(
      `Minimum length is ${rules.minLength} characters (got ${password.length}).`
    );
  }

  if (password.length > rules.maxLength) {
    throw new PasswordComplexityError(
      `Maximum length is ${rules.maxLength} characters (prevents DoS).`
    );
  }

  if (rules.requireUppercase && !/[A-Z]/.test(password)) {
    throw new PasswordComplexityError('Must contain at least one uppercase letter.');
  }

  if (rules.requireLowercase && !/[a-z]/.test(password)) {
    throw new PasswordComplexityError('Must contain at least one lowercase letter.');
  }

  if (rules.requireDigit && !/[0-9]/.test(password)) {
    throw new PasswordComplexityError('Must contain at least one digit.');
  }

  if (rules.requireSpecial && !/[!@#$%^&*()_+\-=\[\]{};':\",./<>?\\|~]/.test(password)) {
    throw new PasswordComplexityError(
      'Must contain at least one special character (!@#$%^&*()_+-=[]{};\':\",./<>?).'
    );
  }
}

export async function registerUser(prisma, { email, password, displayName }) {
  validatePasswordComplexity(password);

  const normalizedEmail = email.trim().toLowerCase();
  if (!normalizedEmail || !normalizedEmail.includes('@')) {
    throw new PasswordComplexityError('Invalid email format.');
  }

  try {
    return await prisma.$transaction(async (tx) => {
      const existing = await findUserByEmail(tx, normalizedEmail);
      if (existing) {
        throw new DuplicateEmailError(normalizedEmail);
      }

      const user = await insertUser(tx, {
        email: normalizedEmail,
        displayName: displayName || null,
      });

      const passwordHash = await hashPassword(password);
      await insertCredential(tx, { userId: user.id, passwordHash });

      console.log(`[AUTH] User registered: ${user.id} (${user.email})`);
      return {
        id: user.id,
        email: user.email,
        role: user.role,
        created_at: user.created_at,
        displayName: user.displayName,
      };
    });
  } catch (err) {
    if (err.code === 'P2002' && err.meta?.target?.includes('email')) {
      throw new DuplicateEmailError(normalizedEmail);
    }
    throw err;
  }
}

export async function loginUser(prisma, { email, password }) {
  const normalizedEmail = (email || '').trim().toLowerCase();
  if (!normalizedEmail || !password) {
    throw new AuthenticationError('Invalid credentials.');
  }

  const user = await findUserByEmail(prisma, normalizedEmail);
  if (!user) {
    throw new AuthenticationError('Invalid credentials.');
  }

  const storedHash = await getPasswordHash(prisma, user.id);
  if (!storedHash) {
    throw new AuthenticationError('Invalid credentials.');
  }

  const isValid = await verifyPassword(password, storedHash);
  if (!isValid) {
    throw new AuthenticationError('Invalid credentials.');
  }

  const fullUser = await prisma.users.findUnique({
    where: { id: user.id },
    select: { id: true, email: true, role: true, display_name: true }
  });

  return {
    id: fullUser.id,
    email: fullUser.email,
    role: fullUser.role,
    displayName: fullUser.display_name,
  };
}

# Build stage
FROM node:20-alpine AS builder

WORKDIR /usr/src/app

# Copy root configurations for npm workspaces
COPY package.json package-lock.json ./
COPY services/auth-service/package.json ./services/auth-service/

# Install dependencies including typescript/ts-node
RUN npm ci

# Copy auth-service source and prisma schemas
COPY services/auth-service/ ./services/auth-service/

# Generate prisma client locally in auth-service
WORKDIR /usr/src/app/services/auth-service
RUN npx prisma generate

# Build TypeScript to Javascript
RUN npm run build

# Run stage
FROM node:20-alpine AS runner

WORKDIR /usr/src/app

# Copy package references
COPY package.json package-lock.json ./
COPY services/auth-service/package.json ./services/auth-service/

# Install only production dependencies
RUN npm ci --omit=dev

# Copy compiled outputs from builder
COPY --from=builder /usr/src/app/services/auth-service/dist ./services/auth-service/dist
COPY --from=builder /usr/src/app/services/auth-service/prisma ./services/auth-service/prisma
COPY --from=builder /usr/src/app/services/auth-service/src/generated/client ./services/auth-service/src/generated/client

EXPOSE 3001

WORKDIR /usr/src/app/services/auth-service

CMD ["node", "dist/index.js"]

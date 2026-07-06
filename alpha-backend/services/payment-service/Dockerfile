# Build stage
FROM node:20-alpine AS builder

WORKDIR /usr/src/app

# Copy root configurations for npm workspaces
COPY package.json package-lock.json ./
COPY services/payment-service/package.json ./services/payment-service/

# Install dependencies
RUN npm ci

# Copy payment-service source and prisma schemas
COPY services/payment-service/ ./services/payment-service/

# Generate prisma client locally in payment-service
WORKDIR /usr/src/app/services/payment-service
RUN npx prisma generate

# Build TypeScript to Javascript
RUN npm run build

# Run stage
FROM node:20-alpine AS runner

WORKDIR /usr/src/app

# Copy package references
COPY package.json package-lock.json ./
COPY services/payment-service/package.json ./services/payment-service/

# Install only production dependencies
RUN npm ci --omit=dev

# Copy compiled outputs from builder
COPY --from=builder /usr/src/app/services/payment-service/dist ./services/payment-service/dist
COPY --from=builder /usr/src/app/services/payment-service/prisma ./services/payment-service/prisma
COPY --from=builder /usr/src/app/services/payment-service/src/generated/client ./services/payment-service/src/generated/client

EXPOSE 3002

WORKDIR /usr/src/app/services/payment-service

CMD ["node", "dist/index.js"]

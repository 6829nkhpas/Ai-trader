/**
 * Find a user by email.
 * @param {import('@prisma/client').PrismaClient} client 
 * @param {string} email
 */
export async function findUserByEmail(client, email) {
  const user = await client.users.findUnique({
    where: { email },
    select: { id: true, email: true }
  });
  return user;
}

/**
 * Find a user by ID.
 * @param {import('@prisma/client').PrismaClient} client
 * @param {string} id
 */
export async function findUserById(client, id) {
  const user = await client.users.findUnique({
    where: { id },
    select: { id: true, email: true, role: true, created_at: true, display_name: true }
  });
  if (!user) return null;
  return {
    ...user,
    displayName: user.display_name
  };
}

/**
 * Insert a new user record.
 * @param {import('@prisma/client').PrismaClient} client
 * @param {{ email: string, displayName: string | null, role: string }} data
 */
export async function insertUser(client, { email, displayName, role = 'user' }) {
  const user = await client.users.create({
    data: {
      email,
      display_name: displayName,
      role
    },
    select: { id: true, email: true, role: true, created_at: true, display_name: true }
  });
  return {
    ...user,
    displayName: user.display_name
  };
}

/**
 * Insert a password credential for a user.
 * @param {import('@prisma/client').PrismaClient} client
 * @param {{ userId: string, passwordHash: string }} data
 */
export async function insertCredential(client, { userId, passwordHash }) {
  await client.user_credentials.create({
    data: {
      user_id: userId,
      credential_type: 'password',
      password_hash: passwordHash
    }
  });
}

/**
 * Fetch the password hash for a user by user/id.
 * @param {import('@prisma/client').PrismaClient} client
 * @param {string} userId
 */
export async function getPasswordHash(client, userId) {
  const credential = await client.user_credentials.findUnique({
    where: {
      user_id_credential_type: {
        user_id: userId,
        credential_type: 'password'
      }
    },
    select: { password_hash: true }
  });
  return credential?.password_hash || null;
}

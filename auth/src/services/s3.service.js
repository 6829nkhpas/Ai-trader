// ──────────────────────────────────────────────────────────────
// services/s3.service.js — Pre-signed URL generation for S3 uploads
// ──────────────────────────────────────────────────────────────

import { S3Client, PutObjectCommand } from '@aws-sdk/client-s3';
import { getSignedUrl } from '@aws-sdk/s3-request-presigner';

let _s3Client = null;

function getS3Client() {
  if (!_s3Client) {
    _s3Client = new S3Client({
      region: process.env.AWS_REGION || 'ap-south-1',
    });
  }
  return _s3Client;
}

/**
 * Generates a pre-signed URL for direct-to-S3 document uploads.
 * @param {string} userId 
 * @param {string} documentType e.g., 'pan', 'aadhaar', 'selfie'
 * @returns {Promise<{url: string, objectKey: string, bucketName: string}>}
 */
export async function generatePresignedUploadUrl(userId, documentType) {
  const bucketName = process.env.AWS_S3_BUCKET || 'ai-trade-kyc-documents';
  const extension = documentType === 'selfie' ? 'jpg' : 'pdf';
  const objectKey = `uploads/${userId}/${documentType}_${Date.now()}.${extension}`;

  const command = new PutObjectCommand({
    Bucket: bucketName,
    Key: objectKey,
    ACL: 'private', 
    ContentType: documentType === 'selfie' ? 'image/jpeg' : 'application/pdf'
  });

  // Strict 10 minute expiration (600 seconds)
  const url = await getSignedUrl(getS3Client(), command, { expiresIn: 600 });

  return { url, objectKey, bucketName };
}

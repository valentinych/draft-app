#!/bin/bash
set -e

# --- Configuration ---
# Ensure these environment variables are set in your CI/CD environment or locally.
# AWS_ACCESS_KEY_ID
# AWS_SECRET_ACCESS_KEY
# AWS_REGION
# AWS_ACCOUNT_ID
# ECR_REPOSITORY_NAME
# ECS_CLUSTER_NAME
# ECS_SERVICE_NAME

ECR_REPOSITORY_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPOSITORY_NAME}"

IMAGE_TAG=$(git rev-parse --short HEAD)

echo "--- Building Docker image ---"
docker build -t python-app:"$IMAGE_TAG" .

echo "--- Authenticating with AWS ECR ---"
aws ecr get-login-password --region "${AWS_REGION}" | \
  docker login --username AWS --password-stdin "${ECR_REPOSITORY_URI}"

echo "--- Tagging Docker image ---"
docker tag python-app:"$IMAGE_TAG" "${ECR_REPOSITORY_URI}:${IMAGE_TAG}"
docker tag python-app:"$IMAGE_TAG" "${ECR_REPOSITORY_URI}:latest"

echo "--- Pushing Docker image to ECR ---"
docker push "${ECR_REPOSITORY_URI}:${IMAGE_TAG}"
docker push "${ECR_REPOSITORY_URI}:latest"

echo "--- Deploying to AWS ECS ---"
aws ecs update-service \
  --cluster "${ECS_CLUSTER_NAME}" \
  --service "${ECS_SERVICE_NAME}" \
  --force-new-deployment \
  --region "${AWS_REGION}"

echo "--- Waiting for ECS service to stabilize ---"
aws ecs wait services-stable \
  --cluster "${ECS_CLUSTER_NAME}" \
  --services "${ECS_SERVICE_NAME}" \
  --region "${AWS_REGION}"

echo "--- Deployment completed successfully ---"

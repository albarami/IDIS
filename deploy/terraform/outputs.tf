# IDIS Terraform Outputs
# Exported values for use by other systems and documentation

# =============================================================================
# VPC Outputs
# =============================================================================

output "vpc_id" {
  description = "ID of the VPC"
  value       = aws_vpc.main.id
}

output "vpc_cidr" {
  description = "CIDR block of the VPC"
  value       = aws_vpc.main.cidr_block
}

output "private_subnet_ids" {
  description = "IDs of private subnets"
  value       = aws_subnet.private[*].id
}

output "public_subnet_ids" {
  description = "IDs of public subnets"
  value       = aws_subnet.public[*].id
}

# =============================================================================
# Database Outputs
# =============================================================================

output "db_endpoint" {
  description = "RDS PostgreSQL endpoint"
  value       = aws_db_instance.main.endpoint
}

output "db_address" {
  description = "RDS PostgreSQL address (hostname only)"
  value       = aws_db_instance.main.address
}

output "db_port" {
  description = "RDS PostgreSQL port"
  value       = aws_db_instance.main.port
}

output "db_name" {
  description = "RDS PostgreSQL database name"
  value       = aws_db_instance.main.db_name
}

output "db_credentials_secret_arn" {
  description = "ARN of the Secrets Manager secret containing database credentials"
  value       = aws_secretsmanager_secret.db_credentials.arn
  sensitive   = true
}

# =============================================================================
# Storage Outputs
# =============================================================================

output "s3_bucket_name" {
  description = "Name of the S3 bucket for object storage"
  value       = aws_s3_bucket.objects.id
}

output "s3_bucket_arn" {
  description = "ARN of the S3 bucket for object storage"
  value       = aws_s3_bucket.objects.arn
}

# =============================================================================
# Security Outputs
# =============================================================================

output "kms_key_arn" {
  description = "ARN of the KMS key used for encryption"
  value       = local.kms_key_arn
  sensitive   = true
}

output "rds_security_group_id" {
  description = "ID of the RDS security group"
  value       = aws_security_group.rds.id
}

# =============================================================================
# Monitoring Outputs
# =============================================================================

output "app_log_group_name" {
  description = "Name of the CloudWatch log group for application logs"
  value       = aws_cloudwatch_log_group.app.name
}

output "audit_log_group_name" {
  description = "Name of the CloudWatch log group for audit logs"
  value       = aws_cloudwatch_log_group.audit.name
}

# =============================================================================
# Connection Strings (for reference)
# =============================================================================

output "database_url_template" {
  description = "Template for IDIS_DATABASE_URL (credentials in Secrets Manager)"
  value       = "postgresql://idis_app:<password>@${aws_db_instance.main.address}:${aws_db_instance.main.port}/${aws_db_instance.main.db_name}"
  sensitive   = true
}

# =============================================================================
# Environment Information
# =============================================================================

output "environment" {
  description = "Deployment environment"
  value       = var.environment
}

output "region" {
  description = "AWS region"
  value       = var.region
}

output "account_id" {
  description = "AWS account ID"
  value       = local.account_id
}

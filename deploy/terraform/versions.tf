# IDIS Terraform Version Constraints
# Pins provider versions for reproducible infrastructure

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    # AWS provider for cloud infrastructure
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }

    # Kubernetes provider for K8s resources
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.25"
    }

    # Helm provider for chart deployments
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.12"
    }

    # Random provider for generating unique identifiers
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Backend configuration - uncomment and configure for production
  # backend "s3" {
  #   bucket         = "idis-terraform-state"
  #   key            = "infrastructure/terraform.tfstate"
  #   region         = "us-east-1"
  #   encrypt        = true
  #   dynamodb_table = "idis-terraform-locks"
  # }
}

#!/usr/bin/env python3
"""
S3 utilities for HR Resume Analyzer Agent
"""
import boto3
import logging
from typing import Optional
from datetime import datetime
import os

logger = logging.getLogger(__name__)

class S3Manager:
    """Manages S3 operations for the HR Resume Analyzer"""
    
    def __init__(self, bucket_name: str = "amzn-s3-resume-analyzer-bucket", region: str = "us-east-1"):
        self.bucket_name = bucket_name
        self.region = region
        self.s3_client = boto3.client('s3', region_name=region)
    
    def upload_file_obj(self, file_obj, key: str) -> bool:
        """Upload file object to S3"""
        try:
            self.s3_client.upload_fileobj(file_obj, self.bucket_name, key)
            logger.info(f"✅ Uploaded file to s3://{self.bucket_name}/{key}")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to upload {key}: {str(e)}")
            return False
    
    def upload_text(self, text: str, key: str) -> bool:
        """Upload text content to S3"""
        try:
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=key,
                Body=text.encode('utf-8'),
                ContentType='text/plain'
            )
            logger.info(f"✅ Uploaded text to s3://{self.bucket_name}/{key}")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to upload text to {key}: {str(e)}")
            return False
    
    def generate_key(self, filename: str, prefix: str = "uploads") -> str:
        """Generate a unique S3 key with timestamp"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"{prefix}/{timestamp}_{filename}"
    
    def ensure_bucket_exists(self) -> bool:
        """Ensure the S3 bucket exists"""
        try:
            self.s3_client.head_bucket(Bucket=self.bucket_name)
            logger.info(f"✅ Bucket {self.bucket_name} exists")
            return True
        except Exception as e:
            logger.error(f"❌ Bucket {self.bucket_name} not accessible: {str(e)}")
            return False
    
    def download_file(self, key: str) -> Optional[str]:
        """Download file content as string"""
        try:
            response = self.s3_client.get_object(Bucket=self.bucket_name, Key=key)
            content = response['Body'].read().decode('utf-8')
            logger.info(f"✅ Downloaded s3://{self.bucket_name}/{key}")
            return content
        except Exception as e:
            logger.error(f"❌ Failed to download {key}: {str(e)}")
            return None

def create_s3_manager(region: str = "us-east-1") -> S3Manager:
    """Create and return an S3Manager instance"""
    return S3Manager(region=region)
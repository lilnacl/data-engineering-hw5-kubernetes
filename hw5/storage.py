import json
import os

import boto3
from botocore.config import Config


def client():
    return boto3.client('s3', endpoint_url=os.environ['S3_ENDPOINT'],
                        aws_access_key_id=os.environ['S3_ACCESS_KEY'],
                        aws_secret_access_key=os.environ['S3_SECRET_KEY'],
                        region_name='us-east-1',
                        config=Config(signature_version='s3v4', s3={'addressing_style': 'path'},
                                      request_checksum_calculation='when_required',
                                      response_checksum_validation='when_required'))


def put_json(s3, key, value):
    body = (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()
    s3.put_object(Bucket='hh-lake', Key=key, Body=body, ContentType='application/json')


def get_json(s3, key):
    response = s3.get_object(Bucket='hh-lake', Key=key)
    with response['Body'] as body:
        return json.load(body)

"""Create local buckets only. Never deletes existing objects."""
import os
import time
import boto3
client = boto3.client('s3', endpoint_url='http://storage:9000', region_name='us-east-1')
for attempt in range(60):
    try:
        client.list_buckets()
        break
    except Exception:
        if attempt == 59: raise RuntimeError('Local object storage not ready') from None
        time.sleep(2)
for bucket in ['userdata', 'signal']:
    existing = {x['Name'] for x in client.list_buckets().get('Buckets', [])}
    if bucket not in existing: client.create_bucket(Bucket=bucket)
print('Local userdata and signal buckets ready.')

import base64
import datetime
import gzip
import json
import os
from datetime import datetime

import boto3
import pytz
import requests

DEFAULT_HEADERS = {
    'Content-type': 'application/json'
}

LOKI_PUSH_API = '{}/api/prom/push'


def __decode_log_data(log_event):
    cw_data = log_event['awslogs']['data']
    compressed_payload = base64.b64decode(cw_data)
    decoded_payload = json.loads(gzip.decompress(compressed_payload))
    return decoded_payload


def __create_labels(log_group):
    cloudwatch_logs = boto3.client('logs', region_name='eu-central-1')
    try:
        response = cloudwatch_logs.list_tags_log_group(logGroupName=log_group)
        tags = response['tags']
        tags.update({'logGroup': log_group})
        return "{" + ", ".join(["=".join([key, '"' + str(val) + '"']) for key, val in tags.items()]) + "}"
    except Exception as e:
        print('Failed to load tags of resource group. Fallback to logGroup group only.')
        return '{logGroup="' + log_group + '"}'


def __create_loki_stream(log_data):
    entries = []
    for e in log_data['logEvents']:
        entries.append({
            'ts': datetime.fromtimestamp(int(e['timestamp']) / 1000, pytz.timezone('UTC')).isoformat('T'),
            'line': e['message']
        })

    return {
        'streams': [
            {
                'labels': __create_labels(log_data['logGroup']),
                'entries': entries
            }
        ]
    }


def lambda_handler(event, context):
    print(event)
    log_data = __decode_log_data(event)
    loki_stream = __create_loki_stream(log_data)
    loki_endpoint = LOKI_PUSH_API.format(os.environ.get('LOKI_ENDPOINT', 'http://localhost:3100'))
    a = requests.post(loki_endpoint, data=json.dumps(loki_stream), headers=DEFAULT_HEADERS)
    if a.status_code != 204:
        print("Failed to write to Loki: " + a.text)

"""
Dummy Lambda function to log to CloudWatch
"""
import logging

logging.getLogger().setLevel(logging.INFO)


def lambda_handler(event, context):
    logging.info(event['name'] + ' started...')
    logging.info("Hey there! This should go to Loki please!")
    logging.info('Config: {}'.format(event))

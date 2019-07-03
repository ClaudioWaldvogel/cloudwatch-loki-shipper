import logging

logging.getLogger().setLevel(logging.INFO)


def lambda_handler(event, context):
    logging.info(event['probe'] + ' started...')
    logging.info('Config: {}'.format(event))

import json

import boto3 as b
import click

REGION = 'eu-central-1'
SHIPPER_NAME = 'loki-log-shipper'


@click.group('cli')
def cli():
    pass


@cli.command(help="Deploy a Loki logging demo application.")
@click.option('-l', '--loki', help='Loki endpoint URL')
@click.argument('action', nargs=1, type=click.Choice(['start', 'stop']))
@click.pass_context
def demo(context, loki, action):
    context.invoke(shipper, loki=loki, action=action)
    for n in ['Probe1', 'Probe2']:
        context.invoke(probe, name=n, action=action)
    print('Demo {}'.format(action))


@cli.command(help="Deploy a mock synthetic probe which is executed once a minute")
@click.option('-n', '--name', required=True, help='TThe name of the probe to be started')
@click.argument('action', nargs=1, type=click.Choice(['start', 'stop']))
def probe(name, action):
    function_name = 'probe-{}'.format(name)
    log_group_name = '/aws/lambda/{}'.format(function_name)
    handler = 'scripts/probe.lambda_handler'

    tags = {
        'probe': name,
        'info': 'Some info for probe: {}'.format(name),
        'location': REGION
    }

    config = {
        'FunctionName': function_name,
        'Runtime': 'python3.7',
        'Role': '',  # Injected on deployment
        'Handler': handler,
        'Code': {'ZipFile': open('../lambda-loki-logging.zip', 'rb').read()},
        'Description': 'Loki Shipper Example Probe: {}'.format(function_name)
    }

    if action == 'start':
        log_group_arn = __create_or_update_log_group(log_group_name, tags)
        function_arn = __start_lambda(config)
        # __create_log_subscription(log_group_name, log_group_arn)
        __create_schedule_event('{}-trigger'.format(function_name), function_arn, tags)

    elif action == 'stop':
        __remove_scheduled_event('{}-trigger'.format(function_name))
        __stop_lambda(config)


@cli.command(help="Deploy Loki shipper lambda function.")
@click.option('-l', '--loki', default='http://localhost:3100', help='Loki endpoint URL')
@click.argument('action', nargs=1, type=click.Choice(['start', 'stop']))
def shipper(loki, action):
    """        Starts/Stops the shipper lambda function    """
    config = {
        'FunctionName': SHIPPER_NAME,
        'Runtime': 'python3.7',
        'Role': '',  # Injected on deployment
        'Handler': 'scripts/loki-shipper.lambda_handler',
        'Code': {'ZipFile': open('../lambda-loki-logging.zip', 'rb').read()},
        'Description': 'Loki Shipper',
        'Environment': {
            'Variables': {
                'LOKI_ENDPOINT': loki
            }
        }
    }
    __start_lambda(config) if action == 'start' else __stop_lambda(config)


@cli.command(help="Attach log group to the Loki shipper")
@click.option('-t', '--tags', required=False, multiple=True, help='Tags to be attached to the log group')
@click.argument('group', nargs=1, type=click.STRING)  # , help='The log group to be attached to shipper.')
def attach(tags, group):
    # Tag log group
    tags_dict = dict((k.strip(), v.strip()) for k, v in (t.split('=') for t in tags)) if tags else None
    __create_or_update_log_group(group, tags_dict)
    __create_log_subscription(group)


# Internal methods

def __create_log_subscription(target_log_group):
    """
    Adds a log subscription for the given log group to the shipper lambda function
    :param target_log_group: The log group to be watched
    :param target_log_group_arn: The arn of the log group
    :return:
    """
    lambda_client = b.client('lambda', region_name=REGION)
    log_client = b.client('logs', region_name=REGION)
    try:
        get_shipper_response = lambda_client.get_function(FunctionName=SHIPPER_NAME)

        try:
            describe_log_groups_response = log_client.describe_log_groups(logGroupNamePrefix=target_log_group)
            if len(describe_log_groups_response['logGroups']) == 0:
                print('LogGroup <{}> not available'.format(target_log_group))
            log_group_arn = describe_log_groups_response['logGroups'][0]['arn']

            lambda_client.add_permission(
                FunctionName=SHIPPER_NAME,
                StatementId='{}-loki-log-shipper'.format(target_log_group.split('/')[-1]),
                Action='lambda:InvokeFunction',
                Principal="logs.eu-central-1.amazonaws.com",
                SourceArn=log_group_arn
            )
        except Exception as e:
            if not isinstance(e, lambda_client.exceptions.ResourceConflictException):
                raise

        log_client.put_subscription_filter(
            destinationArn=get_shipper_response['Configuration']['FunctionArn'],
            filterName=target_log_group + '-loki-logger-subscription',
            filterPattern='',
            logGroupName=target_log_group,
        )
        print("Created log subscription for {}, on {}".format(target_log_group, SHIPPER_NAME))

    except Exception as e:
        raise Exception('Failed to create log subscription. Is loki-shipper lambda running?', e)


def __create_schedule_event(event_name, function_arn, function_input):
    """
    Creates a CloudWatch events which triggers the given lambda function once a minute
    :param event_name: The name of the event
    :param function_arn: The arn of the lambda function
    :param function_input: The input for the lambda function
    :return:
    """
    event_client = b.client('events', region_name=REGION)
    lambda_client = b.client('lambda', region_name=REGION)

    # Create the schedule rule
    rule_arn = event_client.put_rule(Name=event_name,
                                     ScheduleExpression='rate(1 minute)',
                                     Description=event_name,
                                     State='ENABLED').get('RuleArn')

    # Attach the lambda target
    target = {'Id': event_name,
              'Arn': function_arn,
              'Input': json.dumps(function_input)}
    event_client.put_targets(Rule=event_name,
                             Targets=[target])

    try:
        # Ensure CloudWatch is allowed to invoke the function
        lambda_client.add_permission(FunctionName=function_arn,
                                     StatementId=event_name,
                                     Action='lambda:InvokeFunction',
                                     Principal='events.amazonaws.com',
                                     SourceArn=rule_arn)
        print('Created CloudWatch event: {}'.format(event_name))
    except Exception as e:
        if not isinstance(e, lambda_client.exceptions.ResourceConflictException):
            raise


def __remove_scheduled_event(event_name):
    """
    Remove a CloudWatch event.
    :param event_name: The event to be removed
    """
    event_client = b.client('events', region_name=REGION)
    try:
        event_client.remove_targets(Rule=event_name, Ids=[event_name])
        event_client.delete_rule(Name=event_name)
        print('Removed CloudWatch event: {}'.format(event_name))
    except Exception as e:
        if not isinstance(e, event_client.exceptions.ResourceNotFoundException):
            raise

        # Cloudwatch Logs utils


def __create_or_update_log_group(log_group_name, tags):
    log_client = b.client('logs', region_name=REGION)
    try:
        if tags:
            log_client.create_log_group(logGroupName=log_group_name, tags=tags)
        else:
            log_client.create_log_group(logGroupName=log_group_name)
    except Exception as e:
        if not isinstance(e, log_client.exceptions.ResourceAlreadyExistsException):
            raise
        elif tags:
            log_client.tag_log_group(logGroupName=log_group_name, tags=tags)

    log_client.put_retention_policy(
        logGroupName=log_group_name,
        retentionInDays=1
    )

    describe_log_groups_response = log_client.describe_log_groups(logGroupNamePrefix=log_group_name)
    print('Created/Updated LogGroup: {}'.format(log_group_name))
    return describe_log_groups_response['logGroups'][0]['arn']


# Lambda utils
def __start_lambda(config):
    # Create the boto client
    lambda_client = b.client('lambda', region_name=REGION)
    iam_client = b.client('iam')
    # Fetch the role arn
    role_response = iam_client.get_role(RoleName='loki-logging-example')
    config.update({'Role': role_response['Role']['Arn']})
    function_name = config['FunctionName']
    try:
        lambda_code_for_update = config.get('Code').copy()
        lambda_code_for_update.update({'FunctionName': function_name})
        update_response = lambda_client.update_function_code(**lambda_code_for_update)
        print(('Updated Lambda: ' + function_name))
        return update_response['FunctionArn']
    except Exception as e:
        if isinstance(e, lambda_client.exceptions.ResourceNotFoundException):
            try:
                create_response = lambda_client.create_function(**config)
                print('Created Lambda: ' + function_name)
                return create_response['FunctionArn']
            except Exception as ex:
                raise Exception('Failed to create Lambda function', ex)
        else:
            raise Exception('Failed to update Lambda function', e)


def __stop_lambda(config):
    lambda_client = b.client('lambda', region_name=REGION)
    function_name = config['FunctionName']
    try:
        lambda_client.delete_function(FunctionName=function_name)
        print('Stopped Lambda Function: ' + function_name)
    except Exception as e:
        if isinstance(e, lambda_client.exceptions.ResourceNotFoundException):
            # if we fail because the resource is not present we are fine
            return
        # Otherwise we rethrow the error
        raise Exception('Failed to stop lambda function: ' + function_name, e)


if __name__ == '__main__':
    cli()

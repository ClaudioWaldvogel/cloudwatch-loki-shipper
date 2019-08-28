import json
import os
import subprocess

import boto3 as b
import click

SHIPPER_NAME = 'loki-shipper'
LOKI_SHIPPER_ROLE_NAME = 'loki-shipper'
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET_DIR = os.path.join(PROJECT_DIR, "target")
SHIPPER_ZIP = os.path.join(TARGET_DIR, 'shipper.zip')
DEMO_LAMBDA_ZIP = os.path.join(TARGET_DIR, 'demo-lambda.zip')


class CliContext(object):

    def __init__(self):
        self.aws_profile = 'default'


# Enable context injection to click commands
pass_context = click.make_pass_decorator(CliContext, ensure=True)


@click.group('cli')
@click.option('-p', '--profile', default='default', help='The aws profile to be used. Needs to be defined in .aws/credentials. Defaults to <default>')
@click.option('-r', '--region', default='eu-central-1', help='The aws region to be used. Defaults to <eu-central-1>')
@pass_context
def cli(context, profile, region):
    context.aws_profile = profile
    context.aws_region = region
    b.setup_default_session(profile_name=context.aws_profile, region_name=region)


@cli.command(help='Build deployment packages')
def package():
    print('Creating deployment packages')
    subprocess.call(['sh', os.path.join(PROJECT_DIR, 'package.sh')])


@cli.command(short_help="Deploy a Loki logging demo application. It will be deployed: The Loki shipper function, 2 demo functions watched by the Loki shipper function.")
@click.option('-l', '--loki', help='Loki endpoint URL')
@click.option('-b', '--build', default=False, is_flag=True, help='Flag indicates if deployment packages should be build')
@click.argument('action', nargs=1, type=click.Choice(['start', 'stop']))
@click.pass_context
def demo(context, loki, action, build=True):
    if action == 'start' and build:
        context.invoke(package)
    context.invoke(shipper, loki=loki, action=action)
    for n in ['func1', 'func2']:
        context.invoke(demofunc, name=n, action=action)
    print('Demo {}'.format(action))


@cli.command(short_help="Deploy a demo lambda function which is executed once a minute. The corresponding log group is watched by shipper function.")
@click.option('-n', '--name', required=True, help='TThe name of the function to be started')
@click.option('-b', '--build', default=False, is_flag=True, help='Flag indicates if deployment packages should be build')
@click.argument('action', nargs=1, type=click.Choice(['start', 'stop']))
@pass_context
def demofunc(context, name, build, action):
    if build or __should_package():
        context.invoke(package)

    function_name = 'demofunc-{}'.format(name)
    log_group_name = '/aws/lambda/{}'.format(function_name)
    handler = 'demo-lambda.lambda_handler'

    tags = {
        'name': name,
        'info': 'Hey there Loki. I am: {}'.format(name),
        'driver': 'Python',
        'location': context.aws_region
    }

    config = {
        'FunctionName': function_name,
        'Runtime': 'python3.7',
        'Role': '',  # Injected on deployment
        'Handler': handler,
        'Code': {'ZipFile': open(DEMO_LAMBDA_ZIP, 'rb').read()},
        'Description': 'Demo function to ship logs to Loki: {}'.format(function_name)
    }

    if action == 'start':
        __create_or_update_log_group(log_group_name, tags)
        function_arn = __start_lambda(config)
        __create_log_subscription(log_group_name)
        __create_schedule_event('{}-trigger'.format(function_name), function_arn, tags)

    elif action == 'stop':
        __remove_scheduled_event('{}-trigger'.format(function_name))
        __stop_lambda(config)


@cli.command(short_help="Deploy Loki shipper lambda function.")
@click.option('-l', '--loki', default='http://localhost:3100', help='Loki endpoint URL')
@click.option('-b', '--build', default=False, is_flag=True, help='Flag indicates if deployment packages should be build')
@click.argument('action', nargs=1, type=click.Choice(['start', 'stop']))
@click.pass_context
def shipper(context, loki, action, build):
    """        Starts/Stops the shipper lambda function    """
    if build or __should_package():
        context.invoke(package)
    config = {
        'FunctionName': SHIPPER_NAME,
        'Runtime': 'python3.7',
        'Role': '',  # Injected on deployment
        'Handler': 'loki-shipper.lambda_handler',
        'Code': {'ZipFile': open(SHIPPER_ZIP, 'rb').read()},
        'Description': 'Loki Shipper',
        'Environment': {
            'Variables': {
                'LOKI_ENDPOINT': loki
            }
        }
    }
    __start_lambda(config) if action == 'start' else __stop_lambda(config)


@cli.command(short_help="Attach an existing log group to the Loki shipper.")
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
    """
    lambda_client = b.client('lambda')
    log_client = b.client('logs')
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
        print("Created log subscription for {}".format(target_log_group))

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
    event_client = b.client('events')
    lambda_client = b.client('lambda')

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
    event_client = b.client('events')
    try:
        event_client.remove_targets(Rule=event_name, Ids=[event_name])
        event_client.delete_rule(Name=event_name)
        print('Removed CloudWatch event: {}'.format(event_name))
    except Exception as e:
        if not isinstance(e, event_client.exceptions.ResourceNotFoundException):
            raise

        # Cloudwatch Logs utils


def __create_or_update_log_group(log_group_name, tags):
    log_client = b.client('logs')
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
    print("Starting lambda: " + config['FunctionName'])
    # Create the boto client
    lambda_client = b.client('lambda')
    iam_client = b.client('iam')
    # Fetch the role arn
    role_response = iam_client.get_role(RoleName=LOKI_SHIPPER_ROLE_NAME)
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
    lambda_client = b.client('lambda')
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


def __should_package():
    return not os.path.exists(SHIPPER_ZIP) or not os.path.exists(DEMO_LAMBDA_ZIP)


if __name__ == '__main__':
    cli()

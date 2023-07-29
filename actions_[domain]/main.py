from flask import Flask, request
import functions_framework
import logging
logging.basicConfig(level=logging.INFO)
import os
import requests
import json

PROJECT_ID = os.environ.get('PROJECT_ID')

APP = Flask("internal")

@APP.route('/', methods=['GET', 'POST'])
def unknown_operation():
    response = APP.response_class(
        response="Incomplete path, please select an operation",
        status=400,
        mimetype='text/plain')
    return response

@APP.route('/<action>', methods=['POST', ])
def <action>():
    logging.info("Received request: {}".format(request))
    request_json = json.loads(request.data)
    arg1 = request_json["arg1"]

    response = APP.response_class(
        response=json.dumps({"payload":{
            "error": "Not implemented"
            },
            "responseMessage": "Not implemented"}),
        status=200,
        mimetype='text/plain')
    return response
    
    
@functions_framework.http
def actions_[domain](request):
    internal_ctx = APP.test_request_context(path=request.full_path,
                                            method=request.method)
    internal_ctx.request.data = request.data
    internal_ctx.request.headers = request.headers
    internal_ctx.request.args = request.args
    
    APP.config['PRESERVE_CONTEXT_ON_EXCEPTION']=False
    
    return_value = APP.response_class(
        response="Invalid Request", 
        status=400,
        mimetype='text/plain')
    
    try:
        internal_ctx.push()
        return_value = APP.full_dispatch_request()
        logging.info("Request processed: {}".format(return_value))
        internal_ctx.pop()
    except Exception as e:
        logging.error(e)
    return return_value
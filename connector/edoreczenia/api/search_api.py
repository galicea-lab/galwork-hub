# coding: utf-8
import json

from ..api_client import ApiClient
from ..se_model import BaeSearch, BaeSearchResponse

class SearchEngineApi(object):

    def __init__(self, access_token, api_client=None):
        if api_client is None:
            api_client = ApiClient(access_token)
        self.api_client = api_client
        self.request_timeout = 20

    def SearchBAE(self, client_cert_subject : str, bae_search : BaeSearch):
        """
        :param BaeSearch body: Bae Search Request
        :return: BaeSearchResponse
                 If the method is called asynchronously,
                 returns the request thread.
        """

        collection_formats = {}
        path_params = {}
        query_params = []
        header_params = {}
        header_params['ClientCert-Subject'] = client_cert_subject
        form_params = []
        local_var_files = {}

        if type(bae_search) is dict:
          body_params=bae_search
        else:
          body_params = bae_search.model_dump()
        # HTTP header `Accept`
        header_params['Accept'] = self.api_client.select_header_accept(
            ['application/json'])  # noqa: E501

        # HTTP header `Content-Type`
        header_params['Content-Type'] = self.api_client.select_header_content_type(  # noqa: E501
            ['application/json'])  # noqa: E501

        # Authentication setting
        auth_settings = ['oAuthUserAgent']

        return self.api_client.call_api(
            '/search/bae_search', 'POST',
            path_params,
            query_params,
            header_params,
            body=body_params,
            post_params=form_params,
            files=local_var_files,
            response_type='BaeSearchResponse',  # noqa: E501
            auth_settings=auth_settings,
            async_req=False,
            _return_http_data_only=True,
            _preload_content=False,
            _request_timeout=self.request_timeout,
            collection_formats=collection_formats)

    def SearchBAE1(self, bae_search : dict):
        """
        :param BaeSearch body: Bae Search Request
        :return: BaeSearchResponse
                 If the method is called asynchronously,
                 returns the request thread.
        """

        collection_formats = {}
        path_params = {}
        query_params = []
        header_params = {}
        #header_params['ClientCert-Subject'] = client_cert_subject
        form_params = []
        local_var_files = {}


        body_params=bae_search

        # HTTP header `Accept`
        header_params['Accept'] = self.api_client.select_header_accept(
            ['application/json'])  # noqa: E501

        # HTTP header `Content-Type`
        header_params['Content-Type'] = self.api_client.select_header_content_type(  # noqa: E501
            ['application/json'])  # noqa: E501

        # Authentication setting
        auth_settings = ['oAuthUserAgent']

        # nie pochłaniamy wyjątku, wywołujący (EdrConnector/edr_api.py) ma już własny except.
        return self.api_client.call_api(
            '/search/bae_search', 'POST',
            path_params,
            query_params,
            header_params,
            body=body_params,
            post_params=form_params,
            files=local_var_files,
            response_type='dict', #'BaeSearchResponse',  # noqa: E501
            auth_settings=auth_settings,
            async_req=False,
            _return_http_data_only=True,
            _preload_content=False,
            _request_timeout=self.request_timeout,
            collection_formats=collection_formats)

    def SearchBAE2(self, bae_search : dict):
        """
        :param BaeSearch body: Bae Search Request
        :return: BaeSearchResponse
                 If the method is called asynchronously,
                 returns the request thread.
        """

        collection_formats = {}
        path_params = {}
        query_params = []
        header_params = {}
        #header_params['ClientCert-Subject'] = client_cert_subject
        form_params = []
        local_var_files = {}


        body_params=bae_search

        # HTTP header `Accept`
        header_params['Accept'] = self.api_client.select_header_accept(
            ['application/json'])  # noqa: E501

        # HTTP header `Content-Type`
        header_params['Content-Type'] = self.api_client.select_header_content_type(  # noqa: E501
            ['application/json'])  # noqa: E501

        # Authentication setting
        auth_settings = ['oAuthUserAgent']

        return self.api_client.call_api(
            '/search/eda-confirmation', 'POST',
            path_params,
            query_params,
            header_params,
            body=body_params,
            post_params=form_params,
            files=local_var_files,
            response_type='dict', #'BaeSearchResponse',  # noqa: E501
            auth_settings=auth_settings,
            async_req=False,
            _return_http_data_only=True,
            _preload_content=False,
            _request_timeout=self.request_timeout,
            collection_formats=collection_formats)

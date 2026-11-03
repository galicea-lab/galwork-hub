# coding: utf-8
"""
    User Agent API


    Wygenerowany przez: https://github.com/swagger-api/swagger-codegen.git
    Modyfikacja - użycie Pydantic, rezygnacja ze współbieżności i Python2
    Założenie, że parametry są odpowiedniego typu (tuple, dict) i nie trzeba ich tworzyć na podstawie stringów

"""

from .configuration import Configuration

from .rest import *

class ApiClient(object):

    def __init__(self, access_token: str, configuration=None, header_name=None, header_value=None,
                 cookie=None):
        if configuration is None:
            configuration = Configuration(access_token)
        self.configuration = configuration

        self.rest_client = RESTClientObject(configuration)
        self.default_headers = {}
        if header_name is not None:
            self.default_headers[header_name] = header_value
        self.cookie = cookie
        # Set default User-Agent.
        self.user_agent = 'Galwork/1.0.0/python'
        self.last_response=None

    @property
    def user_agent(self):
        """User agent for this API client"""
        return self.default_headers['User-Agent']

    @user_agent.setter
    def user_agent(self, value):
        self.default_headers['User-Agent'] = value

    def set_default_header(self, header_name, header_value):
        self.default_headers[header_name] = header_value

    def select_header_accept(self, accepts):
      """Returns `Accept` based on an array of accepts provided.

          :param accepts: List of headers.
          :return: Accept (e.g. application/json).
          """
      if not accepts:
        return

      accepts = [x.lower() for x in accepts]

      if 'application/json' in accepts:
        return 'application/json'
      else:
        return ', '.join(accepts)

    def select_header_content_type(self, content_types):
      """Returns `Content-Type` based on an array of content_types provided.

          :param content_types: List of content-types.
          :return: Content-Type (e.g. application/json).
          """
      if not content_types:
        return 'application/json'

      content_types = [x.lower() for x in content_types]

      if 'application/json' in content_types or '*/*' in content_types:
        return 'application/json'
      else:
        return content_types[0]

    def call_api(
            self, resource_path, method,
            path_params=None,
            query_params=None,
            header_params=None,
            body=None,
            post_params=None,
            auth_settings=None,
            _return_http_data_only=None,
            _preload_content=True, _request_timeout=None,
        ### do usunięcia
            collection_formats=None,
            files=None,
            async_req=False,
            response_type=None,
           ):
        # header parameters
        header_params = header_params or {}
        header_params.update(self.default_headers)
        self.update_params_for_auth(header_params, query_params, auth_settings)
        if self.cookie:
            header_params['Cookie'] = self.cookie

        # request url
        for k in path_params:
          # specified safe chars, encode everything
          resource_path = resource_path.replace(
            '{%s}' % k, path_params[k]
          )
        if resource_path.find('auth') == 0:
            url = self.configuration.host + resource_path
        elif resource_path.find('search') >= 0:
            url = self.configuration.bae_url + resource_path
        elif resource_path.find('/evidences/')>=0: 
            url=self.configuration.beuis_url + resource_path
        else:
            url = self.configuration.pp_url + resource_path
        if query_params:
          qp = {}
          for (k, v) in query_params:
            if not (v is None):
              qp[k] = v
          query_params = qp
        # perform request and return response
        response_data = self.request(
            method, url, query_params=query_params, headers=header_params,
            post_params=post_params, body=body,
            _preload_content=_preload_content,
            _request_timeout=_request_timeout)

        self.last_response = response_data
        return response_data



    def request(self, method, url, query_params=None, headers=None,
                post_params=None, body=None, _preload_content=True,
                _request_timeout=None):
        """Makes the HTTP request using RESTClient."""
        if method == "GET":
            return self.rest_client.GET(url,
                                        query_params=query_params,
                                        _preload_content=_preload_content,
                                        _request_timeout=_request_timeout,
                                        headers=headers)
        elif method == "HEAD":
            return self.rest_client.HEAD(url,
                                         query_params=query_params,
                                         _preload_content=_preload_content,
                                         _request_timeout=_request_timeout,
                                         headers=headers)
        elif method == "OPTIONS":
            return self.rest_client.OPTIONS(url,
                                            query_params=query_params,
                                            headers=headers,
                                            post_params=post_params,
                                            _preload_content=_preload_content,
                                            _request_timeout=_request_timeout,
                                            body=body)
        elif method == "POST":
            return self.rest_client.POST(url,
                                         query_params=query_params,
                                         headers=headers,
                                         post_params=post_params,
                                         _preload_content=_preload_content,
                                         _request_timeout=_request_timeout,
                                         body=body)
        elif method == "PUT":
            return self.rest_client.PUT(url,
                                        query_params=query_params,
                                        headers=headers,
                                        post_params=post_params,
                                        _preload_content=_preload_content,
                                        _request_timeout=_request_timeout,
                                        body=body)
        elif method == "PATCH":
            return self.rest_client.PATCH(url,
                                          query_params=query_params,
                                          headers=headers,
                                          post_params=post_params,
                                          _preload_content=_preload_content,
                                          _request_timeout=_request_timeout,
                                          body=body)
        elif method == "DELETE":
            return self.rest_client.DELETE(url,
                                           query_params=query_params,
                                           headers=headers,
                                           _preload_content=_preload_content,
                                           _request_timeout=_request_timeout,
                                           body=body)
        else:
            raise ValueError(
                "http method must be `GET`, `HEAD`, `OPTIONS`,"
                " `POST`, `PATCH`, `PUT` or `DELETE`."
            )

    def update_params_for_auth(self, headers, querys, auth_settings):
      """Updates header and query params based on authentication setting.

          :param headers: Header parameters dict to be updated.
          :param querys: Query parameters tuple list to be updated.
          :param auth_settings: Authentication setting identifiers list.
          """
      if not auth_settings:
        return

      for auth in auth_settings:
        aus = self.configuration.auth_settings()
        auth_setting = aus.get(auth)
        if auth_setting:
          if not auth_setting['value']:
            continue
          elif auth_setting['in'] == 'header':
            headers[auth_setting['key']] = auth_setting['value']
          elif auth_setting['in'] == 'query':
            querys.append((auth_setting['key'], auth_setting['value']))
          else:
            raise ValueError(
              'Authentication token must be in `query` or `header`'
            )

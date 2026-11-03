# coding: utf-8

"""
    User Agent API

    <p> Specyfikacja interfejsu skrzynek eDoręczeń </p> <br> <p> <b><font size=\"6\"> Scenariusze:</fint></b> <br> </p> <p> <b>Utwórz wiadomość roboczą</b> <br> POST /{eDeliveryAddress}/drafts </p> <p> <b>Dodaj załącznik</b> <br> Załącznik dodajemy do wcześniej utworzonej wiadomości roboczej.  <br> Dodawanie dużych zalaczników - patrz Zalączniki. <br> POST /{eDeliveryAddress}/drafts/{messageId}/attachments </p> <p> <b>Usuń załącznik</b> <br> DELETE /{eDeliveryAddress}/drafts/{messageId}/attachments/{attachmentId} </p> <p> <b>Pobierz wiadomości robocze</b> <br> GET /{eDeliveryAddress}/drafts <br> Wiadomości robocze można pobrać w trybie - metadata<br> Domyślnie - metadata </p> <p> <b>Pobierz wiadomość roboczą</b> <br> GET /{eDeliveryAddress}/drafts/{messageId} <br> Wiadomość roboczą można pobrać w trzech trybach - full, metadata, minimal <br> Domyślnie - full </p> <p> <b>Usuń wiadomość roboczą</b> <br> DELETE /{eDeliveryAddress}/drafts/{messageId} </p> <p> <b>Wyślij wiadomość/wiadomości</b> <br> POST /{eDeliveryAddress}/messages </p> <p> <b>Pobierz wiadomości</b> <br> GET /{eDeliveryAddress}/messages <br> Wiadomości można pobrać w dwóch trybach - metadata, minimal <br> Domyślnie - minimal </p> <p> <b>Odczytaj wiadomość</b> <br> GET /{eDeliveryAddress}/messages/{messageId} <br> Wiadomość można pobrać w czterech trybach - full, metadata, minimal, fullExtended. <br> Domyślnie - full <br> </p> <p> <b>Pobierz załącznik</b> <br> Z wiadomości <br> GET /{eDeliveryAddress}/messages/{messageId}/attachments/{attachmentId} <br> Z wiadomości roboczej <br> GET /{eDeliveryAddress}/drafts/{messageId}/attachments/{attachmentId} </p> <p> <b>Prześlij wiadomość dalej</b> <br> GET /{eDeliveryAddress}/messages/{messageId} <br> Wyślij wiadomość zachowując threadId wiadomości źródłowej - MessageMetadata.threadId <br> POST /{eDeliveryAddress}/messages </p> <p> <b>Odpowiedz na wiadomość</b> <br> GET /{eDeliveryAddress}/messages/{messageId} <br> Wyślij wiadomość z threadId wiadomości źródłowej - MessageMetadata.threadId <br> POST /{eDeliveryAddress}/messages </p> <p> <b> Usuń wiadomość</b> <br> DELETE /{eDeliveryAddress}/messages/{messageId} </p> <p> <b>Pobierz potwierdzenia</b> <br> GET /{eDeliveryAddress}/messages/{messageId}/evidences </p> <p> <b><font size=\"6\">Uprawnienia:</fint></b> <br> </p> <p> <b>Sposoby uwierzytelniania i autoryzacji. </b> <br> </p> <p> Rozwiązanie opiera się na standardach SAML 2.0, X.509, OAuth 2.0, UMA 2.0. <br> </p> <p> <b>Uwierzytelnienie na serwerze autoryzacyjnym (Authorization Server): </b> <br> </p> <p> W przypadku systemów klasy EZD oraz innych systemów, klient (Client) uwierzytelnia się na serwerze autoryzacyjnym (Authorization Server) certyfikatem X.509. <br> <a href=\"https://datatracker.ietf.org/doc/draft-ietf-oauth-mtls/?include_text=1\">OAuth 2.0 Mutual-TLS Client Authentication and Certificate-Bound Access Tokens</a>  Obywatele (Resource Owner, Requesting Party), uwierzytelniają się  Węzłem Krajowym - SAML 2.0. <br> <a href=\"https://datatracker.ietf.org/doc/rfc7522/?include_text=1\">Security Assertion Markup Language (SAML) 2.0 Profile for OAuth 2.0 Client Authentication and Authorization Grants</a> </p> <p> <b>Autoryzacja:</b> <br> </p> <p> W zależności od kontekstu – potrzeba autoryzacji klienta (Client), bądź też osoby (Requesting Party), autoryzacja OAuth 2.0 lub UMA 2.0. <br> </p> <p> <b>Deployment:</b> <br> </p> <p> Możliwe są dwa podejścia do rozmieszczenia serwerów autoryzacyjnych: <br> <ul> <li> Model scentralizowany – każdy dostawca skrzynek/usług jest zintegrowany z centralnym serwerem autoryzacyjnym. <br> </li> <li> Model rozproszony – każdy dostawca integruje się z własnym/zaufanym serwerem autoryzacyjnym. <br> </li> </ul> </p> <p> W każdym podejściu uwierzytelnianie odbywa się za pomocą certyfikatów X.509 lub Węzła Krajowego. W przyszłości możliwość integracji z innymi zaufanymi dostawcami tożsamości, eID, … <br> </p> <p> <b>Definiowanie uprawnień:</b> <br> </p> <p> Podstawowy model uprawnień zdefiniowany jest w sekcji securitySchemes.<br> Model uprawnień do wiadomości można rozszerzyć bazując na strukturze Label.<br>  <b>Wzorzec definicji uprawnień do wiadomości:</b><br> permission.[ labels =] gdzie permission to uprawnienie zdefiniowane w sekcji securitySchemes - dotyczy uprawnień na poziomie eDeliveryAddress, labels zdefiniowne w MessageControlData.labels  <b>Przykłady:</b><br> <code> {   \"iss\": https://authorization-server.gov.pl,   \"aud\": \"https://epuap.mailbox.pl/abc123/messages\",   \"sub\": \"def456\",   \"scope\": \"write_messages\",   \"iat\": 1458785432,   \"exp\": 1458872439 } </code> read write dla całej zawartości skrzynki<br>  <code> {   \"iss\": \"https://authorization-server.gov.pl\",   \"aud\": \"https://epuap.mailbox.pl/abc123/messages\",   \"sub\": \"def456\",   \"scope\": \"write_messages.labels=INVOICES\",   \"iat\": 1458785432,   \"exp\": 1458872439 } </code> <br> read write dla wiadomości opatrzonych etykietą \"INVOICES\"<br>  <code> {   \"iss\": \"https://authorization-server.gov.pl\",   \"aud\": \"https://epuap.mailbox.pl/abc123/messages\",   \"sub\": \"def456\",   \"scope\": \"write_messages.labels=EPUAP,ZUS\",   \"iat\": 1458785432,   \"exp\": 1458872439 } </code> read write dla wiadomości opatrzonych etykietami \"EPUAP\" lub \"ZUS\"<br>  Analogicznie pozostale endpointy. </p> <p> <b><font size=\"6\"> Inne:</fint></b> <br> </p> <p> <b>Wysyłka hybrydowa</b> Różnica pomiędzy wysyłką elektroniczną a hybrydową polega wyłącznie na innej adresacji wiadomości W przypadku przesyłki hybrydowej w messageAddressData podajemy Adress, który jest adresem fizycznym. Następnie umieszczamy te dane w messageMetadatato. <br> Fizyczne dane adresowe nadawcy również muszą być uzupełnione podczas wysyłki hybrydowej - takie dane umieszczamy MessageMetadatafrom. <br> W przypadku przesyłki elektronicznej w MessageAddressData wypełniamy pole eDeliveryAddress, który jest adresem do wysyłki elektronicznej. <br> MessageMetadata.from jest zawsze adresem elektronicznym. </p>   # noqa: E501

    OpenAPI spec version: 3.0.8
    
    Generated by: https://github.com/swagger-api/swagger-codegen.git
"""

from __future__ import absolute_import

import io
import json
import logging
import re
import ssl

import certifi
# python 2 and python 3 compatibility library
import six
from six.moves.urllib.parse import urlencode

try:
    import urllib3
except ImportError:
    raise ImportError('Swagger python client requires urllib3.')


logger = logging.getLogger(__name__)


class RESTResponse(io.IOBase):

    def __init__(self, resp):
        self.urllib3_response = resp
        self.status = resp.status
        self.reason = resp.reason
        self.data = resp.data

    def getheaders(self):
        """Returns a dictionary of the response headers."""
        return self.urllib3_response.getheaders()

    def getheader(self, name, default=None):
        """Returns a given response header."""
        return self.urllib3_response.getheader(name, default)


class RESTClientObject(object):

    def __init__(self, configuration, pools_size=4, maxsize=None):
        # urllib3.PoolManager will pass all kw parameters to connectionpool
        # https://github.com/shazow/urllib3/blob/f9409436f83aeb79fbaf090181cd81b784f1b8ce/urllib3/poolmanager.py#L75  # noqa: E501
        # https://github.com/shazow/urllib3/blob/f9409436f83aeb79fbaf090181cd81b784f1b8ce/urllib3/connectionpool.py#L680  # noqa: E501
        # maxsize is the number of requests to host that are allowed in parallel  # noqa: E501
        # Custom SSL certificates and client certificates: http://urllib3.readthedocs.io/en/latest/advanced-usage.html  # noqa: E501

        # cert_reqs
        if configuration.verify_ssl:
            cert_reqs = ssl.CERT_REQUIRED
        else:
            cert_reqs = ssl.CERT_NONE

        # ca_certs
        if configuration.ssl_ca_cert:
            ca_certs = configuration.ssl_ca_cert
        else:
            # if not set certificate file, use Mozilla's root certificates.
            ca_certs = certifi.where()

        addition_pool_args = {}
        if configuration.assert_hostname is not None:
            addition_pool_args['assert_hostname'] = configuration.assert_hostname  # noqa: E501

        if maxsize is None:
            if configuration.connection_pool_maxsize is not None:
                maxsize = configuration.connection_pool_maxsize
            else:
                maxsize = 4

        # https pool manager
        if configuration.proxy:
            self.pool_manager = urllib3.ProxyManager(
                num_pools=pools_size,
                maxsize=maxsize,
                cert_reqs=cert_reqs,
                ca_certs=ca_certs,
                cert_file=configuration.cert_file,
                key_file=configuration.key_file,
                proxy_url=configuration.proxy,
                **addition_pool_args
            )
        else:
            self.pool_manager = urllib3.PoolManager(
                num_pools=pools_size,
                maxsize=maxsize,
                cert_reqs=cert_reqs,
                ca_certs=ca_certs,
                cert_file=configuration.cert_file,
                key_file=configuration.key_file,
                **addition_pool_args
            )

    def request(self, method, url, query_params=None, headers=None,
                body=None, post_params=None, _preload_content=True,
                _request_timeout=None):
        """Perform requests.

        :param method: http request method
        :param url: http request url
        :param query_params: query parameters in the url
        :param headers: http request headers
        :param body: request json body, for `application/json`
        :param post_params: request post parameters,
                            `application/x-www-form-urlencoded`
                            and `multipart/form-data`
        :param _preload_content: if False, the urllib3.HTTPResponse object will
                                 be returned without reading/decoding response
                                 data. Default is True.
        :param _request_timeout: timeout setting for this request. If one
                                 number provided, it will be total request
                                 timeout. It can also be a pair (tuple) of
                                 (connection, read) timeouts.
        """
        method = method.upper()
        assert method in ['GET', 'HEAD', 'DELETE', 'POST', 'PUT',
                          'PATCH', 'OPTIONS']

        if post_params and body:
            raise ValueError(
                "body parameter cannot be used with post_params parameter."
            )

        post_params = post_params or {}
        headers = headers or {}

        timeout = None
        if _request_timeout:
            if isinstance(_request_timeout, (int, ) if six.PY3 else (int, long)):  # noqa: E501,F821
                timeout = urllib3.Timeout(total=_request_timeout)
            elif (isinstance(_request_timeout, tuple) and
                  len(_request_timeout) == 2):
                timeout = urllib3.Timeout(
                    connect=_request_timeout[0], read=_request_timeout[1])

        if 'Content-Type' not in headers:
            headers['Content-Type'] = 'application/json'

        try:
            # For `POST`, `PUT`, `PATCH`, `OPTIONS`, `DELETE`
            if method in ['POST', 'PUT', 'PATCH', 'OPTIONS', 'DELETE']:
                if query_params:
                    url += '?' + urlencode(query_params)
                if re.search('json', headers['Content-Type'], re.IGNORECASE):
                    request_body = '{}'
                    if body is not None:
                        request_body = json.dumps(body)
                    r = self.pool_manager.request(
                        method, url,
                        body=request_body,
                        preload_content=_preload_content,
                        timeout=timeout,
                        headers=headers)
                elif headers['Content-Type'] == 'application/x-www-form-urlencoded':  # noqa: E501
                    r = self.pool_manager.request(
                        method, url,
                        fields=post_params,
                        encode_multipart=False,
                        preload_content=_preload_content,
                        timeout=timeout,
                        headers=headers)
                elif headers['Content-Type'] == 'multipart/form-data':
                    # must del headers['Content-Type'], or the correct
                    # Content-Type which generated by urllib3 will be
                    # overwritten.
                    del headers['Content-Type']
                    r = self.pool_manager.request(
                        method, url,
                        fields=post_params,
                        encode_multipart=True,
                        preload_content=_preload_content,
                        timeout=timeout,
                        headers=headers)
                # Pass a `string` parameter directly in the body to support
                # other content types than Json when `body` argument is
                # provided in serialized form
                elif isinstance(body, str):
                    request_body = body
                    r = self.pool_manager.request(
                        method, url,
                        body=request_body,
                        preload_content=_preload_content,
                        timeout=timeout,
                        headers=headers)
                else:
                    # Cannot generate the request from given parameters
                    msg = """Cannot prepare a request message for provided
                             arguments. Please check that your arguments match
                             declared content type."""
                    raise ApiException(status=0, reason=msg)
            # For `GET`, `HEAD`
            else:
                r = self.pool_manager.request(method, url,
                                              fields=query_params,
                                              preload_content=_preload_content,
                                              timeout=timeout,
                                              headers=headers)
        except urllib3.exceptions.SSLError as e:
            msg = "{0}\n{1}".format(type(e).__name__, str(e))
            raise ApiException(status=0, reason=msg)

        if _preload_content:
            r = RESTResponse(r)

            # log response body
            logger.debug("response body: %s", r.data)

        if not 200 <= r.status <= 299:
            raise ApiException(http_resp=r)

        return r

    def GET(self, url, headers=None, query_params=None, _preload_content=True,
            _request_timeout=None):
        return self.request("GET", url,
                            headers=headers,
                            _preload_content=_preload_content,
                            _request_timeout=_request_timeout,
                            query_params=query_params)

    def HEAD(self, url, headers=None, query_params=None, _preload_content=True,
             _request_timeout=None):
        return self.request("HEAD", url,
                            headers=headers,
                            _preload_content=_preload_content,
                            _request_timeout=_request_timeout,
                            query_params=query_params)

    def OPTIONS(self, url, headers=None, query_params=None, post_params=None,
                body=None, _preload_content=True, _request_timeout=None):
        return self.request("OPTIONS", url,
                            headers=headers,
                            query_params=query_params,
                            post_params=post_params,
                            _preload_content=_preload_content,
                            _request_timeout=_request_timeout,
                            body=body)

    def DELETE(self, url, headers=None, query_params=None, body=None,
               _preload_content=True, _request_timeout=None):
        return self.request("DELETE", url,
                            headers=headers,
                            query_params=query_params,
                            _preload_content=_preload_content,
                            _request_timeout=_request_timeout,
                            body=body)

    def POST(self, url, headers=None, query_params=None, post_params=None,
             body=None, _preload_content=True, _request_timeout=None):
        return self.request("POST", url,
                            headers=headers,
                            query_params=query_params,
                            post_params=post_params,
                            _preload_content=_preload_content,
                            _request_timeout=_request_timeout,
                            body=body)

    def PUT(self, url, headers=None, query_params=None, post_params=None,
            body=None, _preload_content=True, _request_timeout=None):
        return self.request("PUT", url,
                            headers=headers,
                            query_params=query_params,
                            post_params=post_params,
                            _preload_content=_preload_content,
                            _request_timeout=_request_timeout,
                            body=body)

    def PATCH(self, url, headers=None, query_params=None, post_params=None,
              body=None, _preload_content=True, _request_timeout=None):
        return self.request("PATCH", url,
                            headers=headers,
                            query_params=query_params,
                            post_params=post_params,
                            _preload_content=_preload_content,
                            _request_timeout=_request_timeout,
                            body=body)


class ApiException(Exception):

    def __init__(self, status=None, reason=None, http_resp=None):
        if http_resp:
            self.status = http_resp.status
            self.reason = http_resp.reason
            self.body = http_resp.data
            self.headers = http_resp.getheaders()
        else:
            self.status = status
            self.reason = reason
            self.body = None
            self.headers = None

    def __str__(self):
        """Custom error messages for exception"""
        error_message = "({0})\n"\
                        "Reason: {1}\n".format(self.status, self.reason)
        if self.headers:
            error_message += "HTTP response headers: {0}\n".format(
                self.headers)

        if self.body:
            error_message += "HTTP response body: {0}\n".format(self.body)

        return error_message

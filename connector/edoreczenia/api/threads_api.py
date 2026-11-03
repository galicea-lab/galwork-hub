# coding: utf-8

"""
    User Agent API

    <p> Specyfikacja interfejsu skrzynek eDoręczeń </p> <br> <p> <b><font size=\"6\"> Scenariusze:</fint></b> <br> </p> <p> <b>Utwórz wiadomość roboczą</b> <br> POST /{eDeliveryAddress}/drafts </p> <p> <b>Dodaj załącznik</b> <br> Załącznik dodajemy do wcześniej utworzonej wiadomości roboczej.  <br> Dodawanie dużych zalaczników - patrz Zalączniki. <br> POST /{eDeliveryAddress}/drafts/{messageId}/attachments </p> <p> <b>Usuń załącznik</b> <br> DELETE /{eDeliveryAddress}/drafts/{messageId}/attachments/{attachmentId} </p> <p> <b>Pobierz wiadomości robocze</b> <br> GET /{eDeliveryAddress}/drafts <br> Wiadomości robocze można pobrać w trybie - metadata<br> Domyślnie - metadata </p> <p> <b>Pobierz wiadomość roboczą</b> <br> GET /{eDeliveryAddress}/drafts/{messageId} <br> Wiadomość roboczą można pobrać w trzech trybach - full, metadata, minimal <br> Domyślnie - full </p> <p> <b>Usuń wiadomość roboczą</b> <br> DELETE /{eDeliveryAddress}/drafts/{messageId} </p> <p> <b>Wyślij wiadomość/wiadomości</b> <br> POST /{eDeliveryAddress}/messages </p> <p> <b>Pobierz wiadomości</b> <br> GET /{eDeliveryAddress}/messages <br> Wiadomości można pobrać w dwóch trybach - metadata, minimal <br> Domyślnie - minimal </p> <p> <b>Odczytaj wiadomość</b> <br> GET /{eDeliveryAddress}/messages/{messageId} <br> Wiadomość można pobrać w czterech trybach - full, metadata, minimal, fullExtended. <br> Domyślnie - full <br> </p> <p> <b>Pobierz załącznik</b> <br> Z wiadomości <br> GET /{eDeliveryAddress}/messages/{messageId}/attachments/{attachmentId} <br> Z wiadomości roboczej <br> GET /{eDeliveryAddress}/drafts/{messageId}/attachments/{attachmentId} </p> <p> <b>Prześlij wiadomość dalej</b> <br> GET /{eDeliveryAddress}/messages/{messageId} <br> Wyślij wiadomość zachowując threadId wiadomości źródłowej - MessageMetadata.threadId <br> POST /{eDeliveryAddress}/messages </p> <p> <b>Odpowiedz na wiadomość</b> <br> GET /{eDeliveryAddress}/messages/{messageId} <br> Wyślij wiadomość z threadId wiadomości źródłowej - MessageMetadata.threadId <br> POST /{eDeliveryAddress}/messages </p> <p> <b> Usuń wiadomość</b> <br> DELETE /{eDeliveryAddress}/messages/{messageId} </p> <p> <b>Pobierz potwierdzenia</b> <br> GET /{eDeliveryAddress}/messages/{messageId}/evidences </p> <p> <b><font size=\"6\">Uprawnienia:</fint></b> <br> </p> <p> <b>Sposoby uwierzytelniania i autoryzacji. </b> <br> </p> <p> Rozwiązanie opiera się na standardach SAML 2.0, X.509, OAuth 2.0, UMA 2.0. <br> </p> <p> <b>Uwierzytelnienie na serwerze autoryzacyjnym (Authorization Server): </b> <br> </p> <p> W przypadku systemów klasy EZD oraz innych systemów, klient (Client) uwierzytelnia się na serwerze autoryzacyjnym (Authorization Server) certyfikatem X.509. <br> <a href=\"https://datatracker.ietf.org/doc/draft-ietf-oauth-mtls/?include_text=1\">OAuth 2.0 Mutual-TLS Client Authentication and Certificate-Bound Access Tokens</a>  Obywatele (Resource Owner, Requesting Party), uwierzytelniają się  Węzłem Krajowym - SAML 2.0. <br> <a href=\"https://datatracker.ietf.org/doc/rfc7522/?include_text=1\">Security Assertion Markup Language (SAML) 2.0 Profile for OAuth 2.0 Client Authentication and Authorization Grants</a> </p> <p> <b>Autoryzacja:</b> <br> </p> <p> W zależności od kontekstu – potrzeba autoryzacji klienta (Client), bądź też osoby (Requesting Party), autoryzacja OAuth 2.0 lub UMA 2.0. <br> </p> <p> <b>Deployment:</b> <br> </p> <p> Możliwe są dwa podejścia do rozmieszczenia serwerów autoryzacyjnych: <br> <ul> <li> Model scentralizowany – każdy dostawca skrzynek/usług jest zintegrowany z centralnym serwerem autoryzacyjnym. <br> </li> <li> Model rozproszony – każdy dostawca integruje się z własnym/zaufanym serwerem autoryzacyjnym. <br> </li> </ul> </p> <p> W każdym podejściu uwierzytelnianie odbywa się za pomocą certyfikatów X.509 lub Węzła Krajowego. W przyszłości możliwość integracji z innymi zaufanymi dostawcami tożsamości, eID, … <br> </p> <p> <b>Definiowanie uprawnień:</b> <br> </p> <p> Podstawowy model uprawnień zdefiniowany jest w sekcji securitySchemes.<br> Model uprawnień do wiadomości można rozszerzyć bazując na strukturze Label.<br>  <b>Wzorzec definicji uprawnień do wiadomości:</b><br> permission.[ labels =] gdzie permission to uprawnienie zdefiniowane w sekcji securitySchemes - dotyczy uprawnień na poziomie eDeliveryAddress, labels zdefiniowne w MessageControlData.labels  <b>Przykłady:</b><br> <code> {   \"iss\": https://authorization-server.gov.pl,   \"aud\": \"https://epuap.mailbox.pl/abc123/messages\",   \"sub\": \"def456\",   \"scope\": \"write_messages\",   \"iat\": 1458785432,   \"exp\": 1458872439 } </code> read write dla całej zawartości skrzynki<br>  <code> {   \"iss\": \"https://authorization-server.gov.pl\",   \"aud\": \"https://epuap.mailbox.pl/abc123/messages\",   \"sub\": \"def456\",   \"scope\": \"write_messages.labels=INVOICES\",   \"iat\": 1458785432,   \"exp\": 1458872439 } </code> <br> read write dla wiadomości opatrzonych etykietą \"INVOICES\"<br>  <code> {   \"iss\": \"https://authorization-server.gov.pl\",   \"aud\": \"https://epuap.mailbox.pl/abc123/messages\",   \"sub\": \"def456\",   \"scope\": \"write_messages.labels=EPUAP,ZUS\",   \"iat\": 1458785432,   \"exp\": 1458872439 } </code> read write dla wiadomości opatrzonych etykietami \"EPUAP\" lub \"ZUS\"<br>  Analogicznie pozostale endpointy. </p> <p> <b><font size=\"6\"> Inne:</fint></b> <br> </p> <p> <b>Wysyłka hybrydowa</b> Różnica pomiędzy wysyłką elektroniczną a hybrydową polega wyłącznie na innej adresacji wiadomości W przypadku przesyłki hybrydowej w messageAddressData podajemy Adress, który jest adresem fizycznym. Następnie umieszczamy te dane w messageMetadatato. <br> Fizyczne dane adresowe nadawcy również muszą być uzupełnione podczas wysyłki hybrydowej - takie dane umieszczamy MessageMetadatafrom. <br> W przypadku przesyłki elektronicznej w MessageAddressData wypełniamy pole eDeliveryAddress, który jest adresem do wysyłki elektronicznej. <br> MessageMetadata.from jest zawsze adresem elektronicznym. </p>   # noqa: E501

    OpenAPI spec version: 3.0.8
    
    Generated by: https://github.com/swagger-api/swagger-codegen.git
"""

from __future__ import absolute_import

import re  # noqa: F401

# python 2 and python 3 compatibility library
import six

from ..api_client import ApiClient


class ThreadsApi(object):
    """NOTE: This class is auto generated by the swagger code generator program.

    Do not edit the class manually.
    Ref: https://github.com/swagger-api/swagger-codegen
    """

    def __init__(self, access_token, api_client=None):
        if api_client is None:
            api_client = ApiClient(access_token)
        self.api_client = api_client

    def e_delivery_address_threads_get(self, e_delivery_address, **kwargs):  # noqa: E501
        """Get list of threads  # noqa: E501

        Pobranie listy wątków  # noqa: E501
        This method makes a synchronous HTTP request by default. To make an
        asynchronous HTTP request, please pass async_req=True
        >>> thread = api.e_delivery_address_threads_get(e_delivery_address, async_req=True)
        >>> result = thread.get()

        :param async_req bool
        :param str e_delivery_address: Identyfikator adresu/skrzynki. (required)
        :param str sender: Nadawca - imię i nazwisko, nazwa albo ADE
        :param str recipient: Adresat - imię i nazwisko, nazwa albo ADE
        :param str case_id: Numer sprawy
        :param int offset: Liczba elementów, które należy pominąć.
        :param int limit: Liczba elementów które należy zwrócić.
        :param datetime submission_date_from: Data od dla pola submissionDate
        :param datetime submission_date_to: Data do dla pola submissionDate.
        :param datetime event_date_from: Data od dla pola eventDate
        :param datetime event_date_to: Data do dla pola eventDate
        :param str message_title: Tytuł wiadomości
        :param str sort_direction: Kierunek sortowania
        :param str sort_column: Kolumna po której ma się odbywać sortowanie
        :return: Threads
                 If the method is called asynchronously,
                 returns the request thread.
        """
        kwargs['_return_http_data_only'] = True
        if kwargs.get('async_req'):
            return self.e_delivery_address_threads_get_with_http_info(e_delivery_address, **kwargs)  # noqa: E501
        else:
            (data) = self.e_delivery_address_threads_get_with_http_info(e_delivery_address, **kwargs)  # noqa: E501
            return data

    def e_delivery_address_threads_get_with_http_info(self, e_delivery_address, **kwargs):  # noqa: E501
        """Get list of threads  # noqa: E501

        Pobranie listy wątków  # noqa: E501
        This method makes a synchronous HTTP request by default. To make an
        asynchronous HTTP request, please pass async_req=True
        >>> thread = api.e_delivery_address_threads_get_with_http_info(e_delivery_address, async_req=True)
        >>> result = thread.get()

        :param async_req bool
        :param str e_delivery_address: Identyfikator adresu/skrzynki. (required)
        :param str sender: Nadawca - imię i nazwisko, nazwa albo ADE
        :param str recipient: Adresat - imię i nazwisko, nazwa albo ADE
        :param str case_id: Numer sprawy
        :param int offset: Liczba elementów, które należy pominąć.
        :param int limit: Liczba elementów które należy zwrócić.
        :param datetime submission_date_from: Data od dla pola submissionDate
        :param datetime submission_date_to: Data do dla pola submissionDate.
        :param datetime event_date_from: Data od dla pola eventDate
        :param datetime event_date_to: Data do dla pola eventDate
        :param str message_title: Tytuł wiadomości
        :param str sort_direction: Kierunek sortowania
        :param str sort_column: Kolumna po której ma się odbywać sortowanie
        :return: Threads
                 If the method is called asynchronously,
                 returns the request thread.
        """

        all_params = ['e_delivery_address', 'sender', 'recipient', 'case_id', 'offset', 'limit', 'submission_date_from', 'submission_date_to', 'event_date_from', 'event_date_to', 'message_title', 'sort_direction', 'sort_column']  # noqa: E501
        all_params.append('async_req')
        all_params.append('_return_http_data_only')
        all_params.append('_preload_content')
        all_params.append('_request_timeout')

        params = locals()
        for key, val in six.iteritems(params['kwargs']):
            if key not in all_params:
                raise TypeError(
                    "Got an unexpected keyword argument '%s'"
                    " to method e_delivery_address_threads_get" % key
                )
            params[key] = val
        del params['kwargs']
        # verify the required parameter 'e_delivery_address' is set
        if ('e_delivery_address' not in params or
                params['e_delivery_address'] is None):
            raise ValueError("Missing the required parameter `e_delivery_address` when calling `e_delivery_address_threads_get`")  # noqa: E501

        collection_formats = {}

        path_params = {}
        if 'e_delivery_address' in params:
            path_params['eDeliveryAddress'] = params['e_delivery_address']  # noqa: E501

        query_params = []
        if 'sender' in params:
            query_params.append(('sender', params['sender']))  # noqa: E501
        if 'recipient' in params:
            query_params.append(('recipient', params['recipient']))  # noqa: E501
        if 'case_id' in params:
            query_params.append(('caseId', params['case_id']))  # noqa: E501
        if 'offset' in params:
            query_params.append(('offset', params['offset']))  # noqa: E501
        if 'limit' in params:
            query_params.append(('limit', params['limit']))  # noqa: E501
        if 'submission_date_from' in params:
            query_params.append(('submissionDateFrom', params['submission_date_from']))  # noqa: E501
        if 'submission_date_to' in params:
            query_params.append(('submissionDateTo', params['submission_date_to']))  # noqa: E501
        if 'event_date_from' in params:
            query_params.append(('eventDateFrom', params['event_date_from']))  # noqa: E501
        if 'event_date_to' in params:
            query_params.append(('eventDateTo', params['event_date_to']))  # noqa: E501
        if 'message_title' in params:
            query_params.append(('messageTitle', params['message_title']))  # noqa: E501
        if 'sort_direction' in params:
            query_params.append(('sortDirection', params['sort_direction']))  # noqa: E501
        if 'sort_column' in params:
            query_params.append(('sortColumn', params['sort_column']))  # noqa: E501

        header_params = {}

        form_params = []
        local_var_files = {}

        body_params = None
        # HTTP header `Accept`
        header_params['Accept'] = self.api_client.select_header_accept(
            ['application/json'])  # noqa: E501

        # Authentication setting
        auth_settings = ['oAuthUserAgent']  # noqa: E501

        return self.api_client.call_api(
            '/{eDeliveryAddress}/threads', 'GET',
            path_params,
            query_params,
            header_params,
            body=body_params,
            post_params=form_params,
            files=local_var_files,
            response_type='Threads',  # noqa: E501
            auth_settings=auth_settings,
            async_req=params.get('async_req'),
            _return_http_data_only=params.get('_return_http_data_only'),
            _preload_content=params.get('_preload_content', True),
            _request_timeout=params.get('_request_timeout'),
            collection_formats=collection_formats)

    def e_delivery_address_threads_thread_id_get(self, e_delivery_address, thread_id, **kwargs):  # noqa: E501
        """Get thread by id  # noqa: E501

        Pobranie wątku po identyfikatorze  # noqa: E501
        This method makes a synchronous HTTP request by default. To make an
        asynchronous HTTP request, please pass async_req=True
        >>> thread = api.e_delivery_address_threads_thread_id_get(e_delivery_address, thread_id, async_req=True)
        >>> result = thread.get()

        :param async_req bool
        :param str e_delivery_address: Identyfikator adresu/skrzynki. (required)
        :param str thread_id: Identyfikator wątku. (required)
        :param str sender: Nadawca - imię i nazwisko, nazwa albo ADE
        :param str recipient: Adresat - imię i nazwisko, nazwa albo ADE
        :param str case_id: Numer sprawy
        :param datetime submission_date_from: Data od dla pola submissionDate
        :param datetime submission_date_to: Data do dla pola submissionDate.
        :param datetime event_date_from: Data od dla pola eventDate
        :param datetime event_date_to: Data do dla pola eventDate
        :param str message_title: Tytuł wiadomości
        :param str sort_direction: Kierunek sortowania
        :param int offset: Liczba wiadomości, które należy pominąć.
        :param int limit: Limit wiadomości
        :param str sort_column: Kolumna po której ma się odbywać sortowanie
        :return: Thread
                 If the method is called asynchronously,
                 returns the request thread.
        """
        kwargs['_return_http_data_only'] = True
        if kwargs.get('async_req'):
            return self.e_delivery_address_threads_thread_id_get_with_http_info(e_delivery_address, thread_id, **kwargs)  # noqa: E501
        else:
            (data) = self.e_delivery_address_threads_thread_id_get_with_http_info(e_delivery_address, thread_id, **kwargs)  # noqa: E501
            return data

    def e_delivery_address_threads_thread_id_get_with_http_info(self, e_delivery_address, thread_id, **kwargs):  # noqa: E501
        """Get thread by id  # noqa: E501

        Pobranie wątku po identyfikatorze  # noqa: E501
        This method makes a synchronous HTTP request by default. To make an
        asynchronous HTTP request, please pass async_req=True
        >>> thread = api.e_delivery_address_threads_thread_id_get_with_http_info(e_delivery_address, thread_id, async_req=True)
        >>> result = thread.get()

        :param async_req bool
        :param str e_delivery_address: Identyfikator adresu/skrzynki. (required)
        :param str thread_id: Identyfikator wątku. (required)
        :param str sender: Nadawca - imię i nazwisko, nazwa albo ADE
        :param str recipient: Adresat - imię i nazwisko, nazwa albo ADE
        :param str case_id: Numer sprawy
        :param datetime submission_date_from: Data od dla pola submissionDate
        :param datetime submission_date_to: Data do dla pola submissionDate.
        :param datetime event_date_from: Data od dla pola eventDate
        :param datetime event_date_to: Data do dla pola eventDate
        :param str message_title: Tytuł wiadomości
        :param str sort_direction: Kierunek sortowania
        :param int offset: Liczba wiadomości, które należy pominąć.
        :param int limit: Limit wiadomości
        :param str sort_column: Kolumna po której ma się odbywać sortowanie
        :return: Thread
                 If the method is called asynchronously,
                 returns the request thread.
        """

        all_params = ['e_delivery_address', 'thread_id', 'sender', 'recipient', 'case_id', 'submission_date_from', 'submission_date_to', 'event_date_from', 'event_date_to', 'message_title', 'sort_direction', 'offset', 'limit', 'sort_column']  # noqa: E501
        all_params.append('async_req')
        all_params.append('_return_http_data_only')
        all_params.append('_preload_content')
        all_params.append('_request_timeout')

        params = locals()
        for key, val in six.iteritems(params['kwargs']):
            if key not in all_params:
                raise TypeError(
                    "Got an unexpected keyword argument '%s'"
                    " to method e_delivery_address_threads_thread_id_get" % key
                )
            params[key] = val
        del params['kwargs']
        # verify the required parameter 'e_delivery_address' is set
        if ('e_delivery_address' not in params or
                params['e_delivery_address'] is None):
            raise ValueError("Missing the required parameter `e_delivery_address` when calling `e_delivery_address_threads_thread_id_get`")  # noqa: E501
        # verify the required parameter 'thread_id' is set
        if ('thread_id' not in params or
                params['thread_id'] is None):
            raise ValueError("Missing the required parameter `thread_id` when calling `e_delivery_address_threads_thread_id_get`")  # noqa: E501

        collection_formats = {}

        path_params = {}
        if 'e_delivery_address' in params:
            path_params['eDeliveryAddress'] = params['e_delivery_address']  # noqa: E501
        if 'thread_id' in params:
            path_params['threadId'] = params['thread_id']  # noqa: E501

        query_params = []
        if 'sender' in params:
            query_params.append(('sender', params['sender']))  # noqa: E501
        if 'recipient' in params:
            query_params.append(('recipient', params['recipient']))  # noqa: E501
        if 'case_id' in params:
            query_params.append(('caseId', params['case_id']))  # noqa: E501
        if 'submission_date_from' in params:
            query_params.append(('submissionDateFrom', params['submission_date_from']))  # noqa: E501
        if 'submission_date_to' in params:
            query_params.append(('submissionDateTo', params['submission_date_to']))  # noqa: E501
        if 'event_date_from' in params:
            query_params.append(('eventDateFrom', params['event_date_from']))  # noqa: E501
        if 'event_date_to' in params:
            query_params.append(('eventDateTo', params['event_date_to']))  # noqa: E501
        if 'message_title' in params:
            query_params.append(('messageTitle', params['message_title']))  # noqa: E501
        if 'sort_direction' in params:
            query_params.append(('sortDirection', params['sort_direction']))  # noqa: E501
        if 'offset' in params:
            query_params.append(('offset', params['offset']))  # noqa: E501
        if 'limit' in params:
            query_params.append(('limit', params['limit']))  # noqa: E501
        if 'sort_column' in params:
            query_params.append(('sortColumn', params['sort_column']))  # noqa: E501

        header_params = {}

        form_params = []
        local_var_files = {}

        body_params = None
        # HTTP header `Accept`
        header_params['Accept'] = self.api_client.select_header_accept(
            ['application/json'])  # noqa: E501

        # Authentication setting
        auth_settings = ['oAuthUserAgent']  # noqa: E501

        return self.api_client.call_api(
            '/{eDeliveryAddress}/threads/{threadId}', 'GET',
            path_params,
            query_params,
            header_params,
            body=body_params,
            post_params=form_params,
            files=local_var_files,
            response_type='Thread',  # noqa: E501
            auth_settings=auth_settings,
            async_req=params.get('async_req'),
            _return_http_data_only=params.get('_return_http_data_only'),
            _preload_content=params.get('_preload_content', True),
            _request_timeout=params.get('_request_timeout'),
            collection_formats=collection_formats)

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


class MessagesApi(object):
    """NOTE: This class is auto generated by the swagger code generator program.

    Do not edit the class manually.
    Ref: https://github.com/swagger-api/swagger-codegen
    """

    def __init__(self, access_token, api_client=None):
        if api_client is None:
            api_client = ApiClient(access_token)
        self.api_client = api_client

    def dictionary_countries_get(self, **kwargs):  # noqa: E501
        """Gets names of countries.  # noqa: E501

        Pobranie listy nazw państw.  # noqa: E501
        This method makes a synchronous HTTP request by default. To make an
        asynchronous HTTP request, please pass async_req=True
        >>> thread = api.dictionary_countries_get(async_req=True)
        >>> result = thread.get()

        :param async_req bool
        :return: list[str]
                 If the method is called asynchronously,
                 returns the request thread.
        """
        kwargs['_return_http_data_only'] = True
        if kwargs.get('async_req'):
            return self.dictionary_countries_get_with_http_info(**kwargs)  # noqa: E501
        else:
            (data) = self.dictionary_countries_get_with_http_info(**kwargs)  # noqa: E501
            return data

    def dictionary_countries_get_with_http_info(self, **kwargs):  # noqa: E501
        """Gets names of countries.  # noqa: E501

        Pobranie listy nazw państw.  # noqa: E501
        This method makes a synchronous HTTP request by default. To make an
        asynchronous HTTP request, please pass async_req=True
        >>> thread = api.dictionary_countries_get_with_http_info(async_req=True)
        >>> result = thread.get()

        :param async_req bool
        :return: list[str]
                 If the method is called asynchronously,
                 returns the request thread.
        """

        all_params = []  # noqa: E501
        all_params.append('async_req')
        all_params.append('_return_http_data_only')
        all_params.append('_preload_content')
        all_params.append('_request_timeout')

        params = locals()
        for key, val in six.iteritems(params['kwargs']):
            if key not in all_params:
                raise TypeError(
                    "Got an unexpected keyword argument '%s'"
                    " to method dictionary_countries_get" % key
                )
            params[key] = val
        del params['kwargs']

        collection_formats = {}

        path_params = {}

        query_params = []

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
            '/dictionary/countries', 'GET',
            path_params,
            query_params,
            header_params,
            body=body_params,
            post_params=form_params,
            files=local_var_files,
            response_type='list[str]',  # noqa: E501
            auth_settings=auth_settings,
            async_req=params.get('async_req'),
            _return_http_data_only=params.get('_return_http_data_only'),
            _preload_content=params.get('_preload_content', True),
            _request_timeout=params.get('_request_timeout'),
            collection_formats=collection_formats)

#    def e_delivery_address_messages_get(self, e_delivery_address, **kwargs):  # noqa: E501
    def getMessages(self, e_delivery_address, **kwargs):  # noqa: E501
        """Lists messages.  # noqa: E501

        Pobranie listy wiadomości.  # noqa: E501
        This method makes a synchronous HTTP request by default. To make an
        asynchronous HTTP request, please pass async_req=True
        >>> thread = api.e_delivery_address_messages_get(e_delivery_address, async_req=True)
        >>> result = thread.get()

        :param async_req bool
        :param str e_delivery_address: Identyfikator adresu/skrzynki. (required)
        :param int offset: Liczba elementów, które należy pominąć.
        :param int limit: Liczba elementów które należy zwrócić.
        :param str format: Format wiadomości - minimalny zakresdanych niezbędny do identyfikacji wiadomości, metadane wiadomości. Zakres trybu metadata - [opened, status, MessageType, labels, from, to ,subject, messageId, refToMessageId, threadId, timestamp, receiptDate, submissionDate, eventDate, shippingService, daysToReceive]
        :param str sender: Nadawca - imię i nazwisko, nazwa albo ADE
        :param bool mass_messages_grouped: Element grupujący dla wiadomości wysłanych w trybie masowym. True - tak, false - nie
        :param str mass_message_id: Identyfikator wysyłki w trybie masowym.
        :param str recipient: Adresat - imię i nazwisko, nazwa albo ADE
        :param str subject: Temat wiadomości
        :param datetime submission_date_from: Data od dla pola submissionDate
        :param datetime submission_date_to: Data do dla pola submissionDate.
        :param datetime event_date_from: Data od dla pola eventDate
        :param datetime event_date_to: Data do dla pola eventDate
        :param datetime receipt_date_from: Data otrzymania od
        :param datetime receipt_date_to: Data otrzymania do
        :param bool attachments: Wiadomość z załącznikiem
        :param str label: Folder do pobrania listy wiadomości
        :param str shipping_service: Rodzaj usługi
        :param str sort_column: Kolumna po której ma się odbywać sortowanie
        :param str sort_direction: Kierunek sortowania
        :param bool opened: Czy wiadomość została już odczytana (odczytanie to pobranie wiadomości po messageId w trybie FULL lub w trybie FULL Extended).
        :return: MessagesWrapper
                 If the method is called asynchronously,
                 returns the request thread.
        """
        kwargs['_return_http_data_only'] = True
        if kwargs.get('async_req'):
            return self.e_delivery_address_messages_get_with_http_info(e_delivery_address, **kwargs)  # noqa: E501
        else:
            (data) = self.e_delivery_address_messages_get_with_http_info(e_delivery_address, **kwargs)  # noqa: E501
            return data

    def e_delivery_address_messages_get_with_http_info(self, e_delivery_address, **kwargs):  # noqa: E501
        """Lists messages.  # noqa: E501

        Pobranie listy wiadomości.  # noqa: E501
        This method makes a synchronous HTTP request by default. To make an
        asynchronous HTTP request, please pass async_req=True
        >>> thread = api.e_delivery_address_messages_get_with_http_info(e_delivery_address, async_req=True)
        >>> result = thread.get()

        :param async_req bool
        :param str e_delivery_address: Identyfikator adresu/skrzynki. (required)
        :param int offset: Liczba elementów, które należy pominąć.
        :param int limit: Liczba elementów które należy zwrócić.
        :param str format: Format wiadomości - minimalny zakresdanych niezbędny do identyfikacji wiadomości, metadane wiadomości. Zakres trybu metadata - [opened, status, MessageType, labels, from, to ,subject, messageId, refToMessageId, threadId, timestamp, receiptDate, submissionDate, eventDate, shippingService, daysToReceive]
        :param str sender: Nadawca - imię i nazwisko, nazwa albo ADE
        :param bool mass_messages_grouped: Element grupujący dla wiadomości wysłanych w trybie masowym. True - tak, false - nie
        :param str mass_message_id: Identyfikator wysyłki w trybie masowym.
        :param str recipient: Adresat - imię i nazwisko, nazwa albo ADE
        :param str subject: Temat wiadomości
        :param datetime submission_date_from: Data od dla pola submissionDate
        :param datetime submission_date_to: Data do dla pola submissionDate.
        :param datetime event_date_from: Data od dla pola eventDate
        :param datetime event_date_to: Data do dla pola eventDate
        :param datetime receipt_date_from: Data otrzymania od
        :param datetime receipt_date_to: Data otrzymania do
        :param bool attachments: Wiadomość z załącznikiem
        :param str label: Folder do pobrania listy wiadomości
        :param str shipping_service: Rodzaj usługi
        :param str sort_column: Kolumna po której ma się odbywać sortowanie
        :param str sort_direction: Kierunek sortowania
        :param bool opened: Czy wiadomość została już odczytana (odczytanie to pobranie wiadomości po messageId w trybie FULL lub w trybie FULL Extended).
        :return: MessagesWrapper
                 If the method is called asynchronously,
                 returns the request thread.
        """

        all_params = ['e_delivery_address', 'offset', 'limit', 'format', 'sender', 'mass_messages_grouped', 'mass_message_id', 'recipient', 'subject', 'submission_date_from', 'submission_date_to', 'event_date_from', 'event_date_to', 'receipt_date_from', 'receipt_date_to', 'attachments', 'label', 'shipping_service', 'sort_column', 'sort_direction', 'opened']  # noqa: E501
        all_params.append('async_req')
        all_params.append('_return_http_data_only')
        all_params.append('_preload_content')
        all_params.append('_request_timeout')

        params = locals()
        for key, val in six.iteritems(params['kwargs']):
            if key not in all_params:
                raise TypeError(
                    "Got an unexpected keyword argument '%s'"
                    " to method e_delivery_address_messages_get" % key
                )
            params[key] = val
        del params['kwargs']
        # verify the required parameter 'e_delivery_address' is set
        if ('e_delivery_address' not in params or
                params['e_delivery_address'] is None):
            raise ValueError("Missing the required parameter `e_delivery_address` when calling `e_delivery_address_messages_get`")  # noqa: E501

        collection_formats = {}

        path_params = {}
        if 'e_delivery_address' in params:
            path_params['eDeliveryAddress'] = params['e_delivery_address']  # noqa: E501

        query_params = []
        if 'offset' in params:
            query_params.append(('offset', params['offset']))  # noqa: E501
        if 'limit' in params:
            query_params.append(('limit', params['limit']))  # noqa: E501
        if 'format' in params:
            query_params.append(('format', params['format']))  # noqa: E501
        if 'sender' in params:
            query_params.append(('sender', params['sender']))  # noqa: E501
        if 'mass_messages_grouped' in params:
            query_params.append(('massMessagesGrouped', params['mass_messages_grouped']))  # noqa: E501
        if 'mass_message_id' in params:
            query_params.append(('massMessageId', params['mass_message_id']))  # noqa: E501
        if 'recipient' in params:
            query_params.append(('recipient', params['recipient']))  # noqa: E501
        if 'subject' in params:
            query_params.append(('subject', params['subject']))  # noqa: E501
        if 'submission_date_from' in params:
            query_params.append(('submissionDateFrom', params['submission_date_from']))  # noqa: E501
        if 'submission_date_to' in params:
            query_params.append(('submissionDateTo', params['submission_date_to']))  # noqa: E501
        if 'event_date_from' in params:
            query_params.append(('eventDateFrom', params['event_date_from']))  # noqa: E501
        if 'event_date_to' in params:
            query_params.append(('eventDateTo', params['event_date_to']))  # noqa: E501
        if 'receipt_date_from' in params:
            query_params.append(('receiptDateFrom', params['receipt_date_from']))  # noqa: E501
        if 'receipt_date_to' in params:
            query_params.append(('receiptDateTo', params['receipt_date_to']))  # noqa: E501
        if 'attachments' in params:
            query_params.append(('attachments', params['attachments']))  # noqa: E501
        if 'label' in params:
            query_params.append(('label', params['label']))  # noqa: E501
        if 'shipping_service' in params:
            query_params.append(('shippingService', params['shipping_service']))  # noqa: E501
        if 'sort_column' in params:
            query_params.append(('sortColumn', params['sort_column']))  # noqa: E501
        if 'sort_direction' in params:
            query_params.append(('sortDirection', params['sort_direction']))  # noqa: E501
        if 'opened' in params:
            query_params.append(('opened', params['opened']))  # noqa: E501

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
            '/{eDeliveryAddress}/messages', 'GET',
            path_params,
            query_params,
            header_params,
            body=body_params,
            post_params=form_params,
            files=local_var_files,
            response_type='MessagesWrapper',  # noqa: E501
            auth_settings=auth_settings,
            async_req=params.get('async_req'),
            _return_http_data_only=params.get('_return_http_data_only'),
            _preload_content=params.get('_preload_content', True),
            _request_timeout=params.get('_request_timeout'),
            collection_formats=collection_formats)

#    def e_delivery_address_messages_message_id_delete(self, e_delivery_address, message_id, **kwargs):  # noqa: E501
    def deleteMessage(self, e_delivery_address, message_id, **kwargs):  # noqa: E501
        """Deletes message.  # noqa: E501

        Usunięcie wiadomości o podanym id.  # noqa: E501
        This method makes a synchronous HTTP request by default. To make an
        asynchronous HTTP request, please pass async_req=True
        >>> thread = api.e_delivery_address_messages_message_id_delete(e_delivery_address, message_id, async_req=True)
        >>> result = thread.get()

        :param async_req bool
        :param str e_delivery_address: Identyfikator adresu/skrzynki. (required)
        :param list[str] message_id: Identyfikator wiadomości (required)
        :return: list[MessageOperationResponseSingleWrapper]
                 If the method is called asynchronously,
                 returns the request thread.
        """
        kwargs['_return_http_data_only'] = True
        if kwargs.get('async_req'):
            return self.e_delivery_address_messages_message_id_delete_with_http_info(e_delivery_address, message_id, **kwargs)  # noqa: E501
        else:
            (data) = self.e_delivery_address_messages_message_id_delete_with_http_info(e_delivery_address, message_id, **kwargs)  # noqa: E501
            return data

    def e_delivery_address_messages_message_id_delete_with_http_info(self, e_delivery_address, message_id, **kwargs):  # noqa: E501
        """Deletes message.  # noqa: E501

        Usunięcie wiadomości o podanym id.  # noqa: E501
        This method makes a synchronous HTTP request by default. To make an
        asynchronous HTTP request, please pass async_req=True
        >>> thread = api.e_delivery_address_messages_message_id_delete_with_http_info(e_delivery_address, message_id, async_req=True)
        >>> result = thread.get()

        :param async_req bool
        :param str e_delivery_address: Identyfikator adresu/skrzynki. (required)
        :param list[str] message_id: Identyfikator wiadomości (required)
        :return: list[MessageOperationResponseSingleWrapper]
                 If the method is called asynchronously,
                 returns the request thread.
        """

        all_params = ['e_delivery_address', 'message_id']  # noqa: E501
        all_params.append('async_req')
        all_params.append('_return_http_data_only')
        all_params.append('_preload_content')
        all_params.append('_request_timeout')

        params = locals()
        for key, val in six.iteritems(params['kwargs']):
            if key not in all_params:
                raise TypeError(
                    "Got an unexpected keyword argument '%s'"
                    " to method e_delivery_address_messages_message_id_delete" % key
                )
            params[key] = val
        del params['kwargs']
        # verify the required parameter 'e_delivery_address' is set
        if ('e_delivery_address' not in params or
                params['e_delivery_address'] is None):
            raise ValueError("Missing the required parameter `e_delivery_address` when calling `e_delivery_address_messages_message_id_delete`")  # noqa: E501
        # verify the required parameter 'message_id' is set
        if ('message_id' not in params or
                params['message_id'] is None):
            raise ValueError("Missing the required parameter `message_id` when calling `e_delivery_address_messages_message_id_delete`")  # noqa: E501

        collection_formats = {}

        path_params = {}
        if 'e_delivery_address' in params:
            path_params['eDeliveryAddress'] = params['e_delivery_address']  # noqa: E501
        if 'message_id' in params:
            path_params['messageId'] = params['message_id']  # noqa: E501
            collection_formats['messageId'] = ''  # noqa: E501

        query_params = []

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
            '/{eDeliveryAddress}/messages/{messageId}', 'DELETE',
            path_params,
            query_params,
            header_params,
            body=body_params,
            post_params=form_params,
            files=local_var_files,
            response_type='list[MessageOperationResponseSingleWrapper]',  # noqa: E501
            auth_settings=auth_settings,
            async_req=params.get('async_req'),
            _return_http_data_only=params.get('_return_http_data_only'),
            _preload_content=params.get('_preload_content', True),
            _request_timeout=params.get('_request_timeout'),
            collection_formats=collection_formats)
#    def e_delivery_address_messages_message_id_get(self, e_delivery_address, message_id, **kwargs):  # noqa: E501
    def getMessage(self, e_delivery_address, message_id, **kwargs):  # noqa: E501
        """Gets message.  # noqa: E501

        Pobranie wiadomości o danym id.  # noqa: E501
        This method makes a synchronous HTTP request by default. To make an
        asynchronous HTTP request, please pass async_req=True
        >>> thread = api.getMessage(e_delivery_address, message_id, async_req=True)
        >>> result = thread.get()

        :param async_req bool
        :param str e_delivery_address: Identyfikator adresu/skrzynki. (required)
        :param list[str] message_id: Identyfikator wiadomości (required)
        :param str format: Format wiadomości - minimalny zakresdanych niezbędny do identyfikacji wiadomości, metadane wiadomości, pełna wiadomość z listą załączników (bez treści), pełna wiadomość z załącznikami. Zakres trybu metadata - [opened, status, MessageType, labels, from, to ,subject, messageId, refToMessageId, threadId, timestamp, receiptDate, submissionDate, eventDate, shippingService]
        :return: list[Message]
                 If the method is called asynchronously,
                 returns the request thread.
        """
        kwargs['_return_http_data_only'] = True
        if kwargs.get('async_req'):
            return self.e_delivery_address_messages_message_id_get_with_http_info(e_delivery_address, message_id, **kwargs)  # noqa: E501
        else:
            (data) = self.e_delivery_address_messages_message_id_get_with_http_info(e_delivery_address, message_id, **kwargs)  # noqa: E501
            return data

    def e_delivery_address_messages_message_id_get_with_http_info(self, e_delivery_address, message_id, **kwargs):  # noqa: E501
        """Gets message.  # noqa: E501

        Pobranie wiadomości o danym id.  # noqa: E501
        This method makes a synchronous HTTP request by default. To make an
        asynchronous HTTP request, please pass async_req=True
        >>> thread = api.e_delivery_address_messages_message_id_get_with_http_info(e_delivery_address, message_id, async_req=True)
        >>> result = thread.get()

        :param async_req bool
        :param str e_delivery_address: Identyfikator adresu/skrzynki. (required)
        :param list[str] message_id: Identyfikator wiadomości (required)
        :param str format: Format wiadomości - minimalny zakresdanych niezbędny do identyfikacji wiadomości, metadane wiadomości, pełna wiadomość z listą załączników (bez treści), pełna wiadomość z załącznikami. Zakres trybu metadata - [opened, status, MessageType, labels, from, to ,subject, messageId, refToMessageId, threadId, timestamp, receiptDate, submissionDate, eventDate, shippingService]
        :return: list[Message]
                 If the method is called asynchronously,
                 returns the request thread.
        """

        all_params = ['e_delivery_address', 'message_id', 'format']  # noqa: E501
        all_params.append('async_req')
        all_params.append('_return_http_data_only')
        all_params.append('_preload_content')
        all_params.append('_request_timeout')

        params = locals()
        for key, val in six.iteritems(params['kwargs']):
            if key not in all_params:
                raise TypeError(
                    "Got an unexpected keyword argument '%s'"
                    " to method getMessage" % key
                )
            params[key] = val
        del params['kwargs']
        # verify the required parameter 'e_delivery_address' is set
        if ('e_delivery_address' not in params or
                params['e_delivery_address'] is None):
            raise ValueError("Missing the required parameter `e_delivery_address` when calling `getMessage`")  # noqa: E501
        # verify the required parameter 'message_id' is set
        if ('message_id' not in params or
                params['message_id'] is None):
            raise ValueError("Missing the required parameter `message_id` when calling `getMessage`")  # noqa: E501

        collection_formats = {}

        path_params = {}
        if 'e_delivery_address' in params:
            path_params['eDeliveryAddress'] = params['e_delivery_address']  # noqa: E501
        if 'message_id' in params:
            path_params['messageId'] = params['message_id']  # noqa: E501
            collection_formats['messageId'] = ''  # noqa: E501

        query_params = []
        if 'format' in params:
            query_params.append(('format', params['format']))  # noqa: E501

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
            '/{eDeliveryAddress}/messages/{messageId}', 'GET',
            path_params,
            query_params,
            header_params,
            body=body_params,
            post_params=form_params,
            files=local_var_files,
            response_type='list[Message]',  # noqa: E501
            auth_settings=auth_settings,
            async_req=params.get('async_req'),
            _return_http_data_only=params.get('_return_http_data_only'),
            _preload_content=params.get('_preload_content', True),
            _request_timeout=params.get('_request_timeout'),
            collection_formats=collection_formats)

    def e_delivery_address_messages_message_id_message_control_data_patch(self, e_delivery_address, message_id, **kwargs):  # noqa: E501
        """Update message control data.  # noqa: E501

        Aktualizacja danych kontrolnych wiadomości.  # noqa: E501
        This method makes a synchronous HTTP request by default. To make an
        asynchronous HTTP request, please pass async_req=True
        >>> thread = api.e_delivery_address_messages_message_id_message_control_data_patch(e_delivery_address, message_id, async_req=True)
        >>> result = thread.get()

        :param async_req bool
        :param str e_delivery_address: Identyfikator adresu/skrzynki. (required)
        :param list[str] message_id: Identyfikator wiadomości. (required)
        :param list[PatchBodyInner] body:
        :return: list[MessageControlDataPatchResponse]
                 If the method is called asynchronously,
                 returns the request thread.
        """
        kwargs['_return_http_data_only'] = True
        if kwargs.get('async_req'):
            return self.e_delivery_address_messages_message_id_message_control_data_patch_with_http_info(e_delivery_address, message_id, **kwargs)  # noqa: E501
        else:
            (data) = self.e_delivery_address_messages_message_id_message_control_data_patch_with_http_info(e_delivery_address, message_id, **kwargs)  # noqa: E501
            return data

    def e_delivery_address_messages_message_id_message_control_data_patch_with_http_info(self, e_delivery_address, message_id, **kwargs):  # noqa: E501
        """Update message control data.  # noqa: E501

        Aktualizacja danych kontrolnych wiadomości.  # noqa: E501
        This method makes a synchronous HTTP request by default. To make an
        asynchronous HTTP request, please pass async_req=True
        >>> thread = api.e_delivery_address_messages_message_id_message_control_data_patch_with_http_info(e_delivery_address, message_id, async_req=True)
        >>> result = thread.get()

        :param async_req bool
        :param str e_delivery_address: Identyfikator adresu/skrzynki. (required)
        :param list[str] message_id: Identyfikator wiadomości. (required)
        :param list[PatchBodyInner] body:
        :return: list[MessageControlDataPatchResponse]
                 If the method is called asynchronously,
                 returns the request thread.
        """

        all_params = ['e_delivery_address', 'message_id', 'body']  # noqa: E501
        all_params.append('async_req')
        all_params.append('_return_http_data_only')
        all_params.append('_preload_content')
        all_params.append('_request_timeout')

        params = locals()
        for key, val in six.iteritems(params['kwargs']):
            if key not in all_params:
                raise TypeError(
                    "Got an unexpected keyword argument '%s'"
                    " to method e_delivery_address_messages_message_id_message_control_data_patch" % key
                )
            params[key] = val
        del params['kwargs']
        # verify the required parameter 'e_delivery_address' is set
        if ('e_delivery_address' not in params or
                params['e_delivery_address'] is None):
            raise ValueError("Missing the required parameter `e_delivery_address` when calling `e_delivery_address_messages_message_id_message_control_data_patch`")  # noqa: E501
        # verify the required parameter 'message_id' is set
        if ('message_id' not in params or
                params['message_id'] is None):
            raise ValueError("Missing the required parameter `message_id` when calling `e_delivery_address_messages_message_id_message_control_data_patch`")  # noqa: E501

        collection_formats = {}

        path_params = {}
        if 'e_delivery_address' in params:
            path_params['eDeliveryAddress'] = params['e_delivery_address']  # noqa: E501
        if 'message_id' in params:
            path_params['messageId'] = params['message_id']  # noqa: E501
            collection_formats['messageId'] = ''  # noqa: E501

        query_params = []

        header_params = {}

        form_params = []
        local_var_files = {}

        body_params = None
        if 'body' in params:
            body_params = params['body']
        # HTTP header `Accept`
        header_params['Accept'] = self.api_client.select_header_accept(
            ['application/json'])  # noqa: E501

        # HTTP header `Content-Type`
        header_params['Content-Type'] = self.api_client.select_header_content_type(  # noqa: E501
            ['application/json'])  # noqa: E501

        # Authentication setting
        auth_settings = ['oAuthUserAgent']  # noqa: E501

        return self.api_client.call_api(
            '/{eDeliveryAddress}/messages/{messageId}/message_control_data', 'PATCH',
            path_params,
            query_params,
            header_params,
            body=body_params,
            post_params=form_params,
            files=local_var_files,
            response_type='list[MessageControlDataPatchResponse]',  # noqa: E501
            auth_settings=auth_settings,
            async_req=params.get('async_req'),
            _return_http_data_only=params.get('_return_http_data_only'),
            _preload_content=params.get('_preload_content', True),
            _request_timeout=params.get('_request_timeout'),
            collection_formats=collection_formats)

#    def e_delivery_address_messages_post(self, body, e_delivery_address, **kwargs):  # noqa: E501
    def postMessage(self, body, e_delivery_address, **kwargs):  # noqa: E501
        """Sends message.  # noqa: E501

        Wysłanie wiadomości.  # noqa: E501
        This method makes a synchronous HTTP request by default. To make an
        asynchronous HTTP request, please pass async_req=True
        >>> thread = api.e_delivery_address_messages_post(body, e_delivery_address, async_req=True)
        >>> result = thread.get()

        :param async_req bool
        :param Message body: Wiadomość (required)
        :param str e_delivery_address: Identyfikator adresu/skrzynki. (required)
        :return: MessageInfo
                 If the method is called asynchronously,
                 returns the request thread.
        """
        kwargs['_return_http_data_only'] = True
        if kwargs.get('async_req'):
            return self.e_delivery_address_messages_post_with_http_info(body, e_delivery_address, **kwargs)  # noqa: E501
        else:
            (data) = self.e_delivery_address_messages_post_with_http_info(body, e_delivery_address, **kwargs)  # noqa: E501
            return data

    def e_delivery_address_messages_post_with_http_info(self, body, e_delivery_address, **kwargs):  # noqa: E501
        """Sends message.  # noqa: E501

        Wysłanie wiadomości.  # noqa: E501
        This method makes a synchronous HTTP request by default. To make an
        asynchronous HTTP request, please pass async_req=True
        >>> thread = api.e_delivery_address_messages_post_with_http_info(body, e_delivery_address, async_req=True)
        >>> result = thread.get()

        :param async_req bool
        :param Message body: Wiadomość (required)
        :param str e_delivery_address: Identyfikator adresu/skrzynki. (required)
        :return: MessageInfo
                 If the method is called asynchronously,
                 returns the request thread.
        """

        all_params = ['body', 'e_delivery_address']  # noqa: E501
        all_params.append('async_req')
        all_params.append('_return_http_data_only')
        all_params.append('_preload_content')
        all_params.append('_request_timeout')

        params = locals()
        for key, val in six.iteritems(params['kwargs']):
            if key not in all_params:
                raise TypeError(
                    "Got an unexpected keyword argument '%s'"
                    " to method e_delivery_address_messages_post" % key
                )
            params[key] = val
        del params['kwargs']
        # verify the required parameter 'body' is set
        if ('body' not in params or
                params['body'] is None):
            raise ValueError("Missing the required parameter `body` when calling `e_delivery_address_messages_post`")  # noqa: E501
        # verify the required parameter 'e_delivery_address' is set
        if ('e_delivery_address' not in params or
                params['e_delivery_address'] is None):
            raise ValueError("Missing the required parameter `e_delivery_address` when calling `e_delivery_address_messages_post`")  # noqa: E501

        collection_formats = {}

        path_params = {}
        if 'e_delivery_address' in params:
            path_params['eDeliveryAddress'] = params['e_delivery_address']  # noqa: E501

        query_params = []

        header_params = {}

        form_params = []
        local_var_files = {}

        body_params = None
        if 'body' in params:
            body_params = params['body']
        # HTTP header `Accept`
        header_params['Accept'] = self.api_client.select_header_accept(
            ['application/json'])  # noqa: E501

        # HTTP header `Content-Type`
        header_params['Content-Type'] = self.api_client.select_header_content_type(  # noqa: E501
            ['application/json'])  # noqa: E501

        # Authentication setting
        auth_settings = ['oAuthUserAgent']  # noqa: E501

        return self.api_client.call_api(
            '/{eDeliveryAddress}/messages', 'POST',
            path_params,
            query_params,
            header_params,
            body=body_params,
            post_params=form_params,
            files=local_var_files,
            response_type='MessageInfo',  # noqa: E501
            auth_settings=auth_settings,
            async_req=params.get('async_req'),
            _return_http_data_only=params.get('_return_http_data_only'),
            _preload_content=params.get('_preload_content', True),
            _request_timeout=params.get('_request_timeout'),
            collection_formats=collection_formats)

    def e_delivery_address_messages_tasks_message_task_id_get(self, e_delivery_address, message_task_id, **kwargs):  # noqa: E501
        """Get message task  # noqa: E501

        Pobranie informacji o zadaniu wysłania wiadomości  # noqa: E501
        This method makes a synchronous HTTP request by default. To make an
        asynchronous HTTP request, please pass async_req=True
        >>> thread = api.e_delivery_address_messages_tasks_message_task_id_get(e_delivery_address, message_task_id, async_req=True)
        >>> result = thread.get()

        :param async_req bool
        :param str e_delivery_address: Identyfikator adresu/skrzynki. (required)
        :param str message_task_id: Identyfikator zadania wysłania wiadomości (required)
        :return: list[MessageOperationResponseWrapperStatus]
                 If the method is called asynchronously,
                 returns the request thread.
        """
        kwargs['_return_http_data_only'] = True
        if kwargs.get('async_req'):
            return self.e_delivery_address_messages_tasks_message_task_id_get_with_http_info(e_delivery_address, message_task_id, **kwargs)  # noqa: E501
        else:
            (data) = self.e_delivery_address_messages_tasks_message_task_id_get_with_http_info(e_delivery_address, message_task_id, **kwargs)  # noqa: E501
            return data

    def e_delivery_address_messages_tasks_message_task_id_get_with_http_info(self, e_delivery_address, message_task_id, **kwargs):  # noqa: E501
        """Get message task  # noqa: E501

        Pobranie informacji o zadaniu wysłania wiadomości  # noqa: E501
        This method makes a synchronous HTTP request by default. To make an
        asynchronous HTTP request, please pass async_req=True
        >>> thread = api.e_delivery_address_messages_tasks_message_task_id_get_with_http_info(e_delivery_address, message_task_id, async_req=True)
        >>> result = thread.get()

        :param async_req bool
        :param str e_delivery_address: Identyfikator adresu/skrzynki. (required)
        :param str message_task_id: Identyfikator zadania wysłania wiadomości (required)
        :return: list[MessageOperationResponseWrapperStatus]
                 If the method is called asynchronously,
                 returns the request thread.
        """

        all_params = ['e_delivery_address', 'message_task_id']  # noqa: E501
        all_params.append('async_req')
        all_params.append('_return_http_data_only')
        all_params.append('_preload_content')
        all_params.append('_request_timeout')

        params = locals()
        for key, val in six.iteritems(params['kwargs']):
            if key not in all_params:
                raise TypeError(
                    "Got an unexpected keyword argument '%s'"
                    " to method e_delivery_address_messages_tasks_message_task_id_get" % key
                )
            params[key] = val
        del params['kwargs']
        # verify the required parameter 'e_delivery_address' is set
        if ('e_delivery_address' not in params or
                params['e_delivery_address'] is None):
            raise ValueError("Missing the required parameter `e_delivery_address` when calling `e_delivery_address_messages_tasks_message_task_id_get`")  # noqa: E501
        # verify the required parameter 'message_task_id' is set
        if ('message_task_id' not in params or
                params['message_task_id'] is None):
            raise ValueError("Missing the required parameter `message_task_id` when calling `e_delivery_address_messages_tasks_message_task_id_get`")  # noqa: E501

        collection_formats = {}

        path_params = {}
        if 'e_delivery_address' in params:
            path_params['eDeliveryAddress'] = params['e_delivery_address']  # noqa: E501
        if 'message_task_id' in params:
            path_params['messageTaskId'] = params['message_task_id']  # noqa: E501

        query_params = []

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
            '/{eDeliveryAddress}/messages/tasks/{messageTaskId}', 'GET',
            path_params,
            query_params,
            header_params,
            body=body_params,
            post_params=form_params,
            files=local_var_files,
            response_type='list[MessageOperationResponseWrapperStatus]',  # noqa: E501
            auth_settings=auth_settings,
            async_req=params.get('async_req'),
            _return_http_data_only=params.get('_return_http_data_only'),
            _preload_content=params.get('_preload_content', True),
            _request_timeout=params.get('_request_timeout'),
            collection_formats=collection_formats)

    def e_delivery_address_messages_tasks_message_task_id_status_get(self, e_delivery_address, message_task_id, **kwargs):  # noqa: E501
        """Get status of message task  # noqa: E501

        Pobranie informacji o zadaniu wysłania wiadomości  # noqa: E501
        This method makes a synchronous HTTP request by default. To make an
        asynchronous HTTP request, please pass async_req=True
        >>> thread = api.e_delivery_address_messages_tasks_message_task_id_status_get(e_delivery_address, message_task_id, async_req=True)
        >>> result = thread.get()

        :param async_req bool
        :param str e_delivery_address: Identyfikator adresu/skrzynki. (required)
        :param str message_task_id: Identyfikator zadania wysłania wiadomości (required)
        :return: MessageTaskStatus
                 If the method is called asynchronously,
                 returns the request thread.
        """
        kwargs['_return_http_data_only'] = True
        if kwargs.get('async_req'):
            return self.e_delivery_address_messages_tasks_message_task_id_status_get_with_http_info(e_delivery_address, message_task_id, **kwargs)  # noqa: E501
        else:
            (data) = self.e_delivery_address_messages_tasks_message_task_id_status_get_with_http_info(e_delivery_address, message_task_id, **kwargs)  # noqa: E501
            return data

    def e_delivery_address_messages_tasks_message_task_id_status_get_with_http_info(self, e_delivery_address, message_task_id, **kwargs):  # noqa: E501
        """Get status of message task  # noqa: E501

        Pobranie informacji o zadaniu wysłania wiadomości  # noqa: E501
        This method makes a synchronous HTTP request by default. To make an
        asynchronous HTTP request, please pass async_req=True
        >>> thread = api.e_delivery_address_messages_tasks_message_task_id_status_get_with_http_info(e_delivery_address, message_task_id, async_req=True)
        >>> result = thread.get()

        :param async_req bool
        :param str e_delivery_address: Identyfikator adresu/skrzynki. (required)
        :param str message_task_id: Identyfikator zadania wysłania wiadomości (required)
        :return: MessageTaskStatus
                 If the method is called asynchronously,
                 returns the request thread.
        """

        all_params = ['e_delivery_address', 'message_task_id']  # noqa: E501
        all_params.append('async_req')
        all_params.append('_return_http_data_only')
        all_params.append('_preload_content')
        all_params.append('_request_timeout')

        params = locals()
        for key, val in six.iteritems(params['kwargs']):
            if key not in all_params:
                raise TypeError(
                    "Got an unexpected keyword argument '%s'"
                    " to method e_delivery_address_messages_tasks_message_task_id_status_get" % key
                )
            params[key] = val
        del params['kwargs']
        # verify the required parameter 'e_delivery_address' is set
        if ('e_delivery_address' not in params or
                params['e_delivery_address'] is None):
            raise ValueError("Missing the required parameter `e_delivery_address` when calling `e_delivery_address_messages_tasks_message_task_id_status_get`")  # noqa: E501
        # verify the required parameter 'message_task_id' is set
        if ('message_task_id' not in params or
                params['message_task_id'] is None):
            raise ValueError("Missing the required parameter `message_task_id` when calling `e_delivery_address_messages_tasks_message_task_id_status_get`")  # noqa: E501

        collection_formats = {}

        path_params = {}
        if 'e_delivery_address' in params:
            path_params['eDeliveryAddress'] = params['e_delivery_address']  # noqa: E501
        if 'message_task_id' in params:
            path_params['messageTaskId'] = params['message_task_id']  # noqa: E501

        query_params = []

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
            '/{eDeliveryAddress}/messages/tasks/{messageTaskId}/status', 'GET',
            path_params,
            query_params,
            header_params,
            body=body_params,
            post_params=form_params,
            files=local_var_files,
            response_type='MessageTaskStatus',  # noqa: E501
            auth_settings=auth_settings,
            async_req=params.get('async_req'),
            _return_http_data_only=params.get('_return_http_data_only'),
            _preload_content=params.get('_preload_content', True),
            _request_timeout=params.get('_request_timeout'),
            collection_formats=collection_formats)

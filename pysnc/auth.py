import requests
import time
import base64
from requests.auth import AuthBase
from urllib3.util import parse_url
from .exceptions import *

JWT_GRANT_TYPE = 'urn:ietf:params:oauth:grant-type:jwt-bearer'


class ServiceNowFlow:
    def authenticate(self, instance: str, **kwargs) -> requests.Session:
        raise AuthenticationException('authenticate not implemented')


# note: not extending LegacyApplicationClient mostly to make oauth libs optional
class ServiceNowPasswordGrantFlow(ServiceNowFlow):

    def __init__(self, username, password, client_id, client_secret):
        """
        Password flow authentication using 'legacy mobile'

        :param username: The user name to authenticate with
        :param password: The user's password
        :param client_id: The ID of the provider
        :param client_secret: Secret for the given provider (client_id)
        """
        if isinstance(username, (tuple, list)):
            self.__username = username[0]
            self.__password = username[1]
        else:
            self.__username = username
            self.__password = password
        self.client_id = client_id
        self.__secret = client_secret

    def authorization_url(self, authorization_base_url):
        return f"{authorization_base_url}/oauth_token.do"

    def authenticate(self, instance: str, **kwargs) -> requests.Session:
        """
        Designed to be called by ServiceNowClient - internal method.
        """
        try:
            from oauthlib.oauth2 import LegacyApplicationClient
            from requests_oauthlib import OAuth2Session  # type: ignore

            oauth = OAuth2Session(client=LegacyApplicationClient(client_id=self.client_id),
                                  auto_refresh_url=self.authorization_url(instance),
                                  auto_refresh_kwargs=dict(client_id=self.client_id, client_secret=self.__secret))
            oauth.fetch_token(token_url=self.authorization_url(instance),
                              username=self.__username, password=self.__password, client_id=self.client_id,
                              client_secret=self.__secret, **kwargs)
            self.__password = None  # no longer need this.
            return oauth
        except ImportError:
            raise AuthenticationException('Install dependency requests-oauthlib')


class ServiceNowJWTAuth(AuthBase):

    def __init__(self, client_id, client_secret, jwt):
        """
        You must obtain a signed JWT from your OIDC provider, e.g. okta or auth0 or the like.
        We then use that JWT to issue an OAuth refresh token, that we then use to auth.
        """
        self.client_id = client_id
        self.__secret = client_secret
        self.__jwt = jwt
        self.__token = None
        self.__expires_at = None

    def _get_access_token(self, request):
        url = parse_url(request.url)
        token_url = f"{url.scheme}://{url.host}/oauth_token.do"
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'Authentication': f"Bearer {self.__jwt}"
        }
        data = {
            'grant_type': JWT_GRANT_TYPE,
            'client_id': self.client_id,
            'client_secret': self.__secret
        }
        r = requests.post(token_url, headers=headers, data=data)
        assert r.status_code == 200, f"Failed to auth, see syslogs {r.text}"
        data = r.json()
        expires = int(time.time()+data['expires_in'])
        return data['access_token'], expires

    def __call__(self, request):
        if not self.__token or time.time() > self.__expires_at:
            self.__token, self.__expires_at = self._get_access_token(request)
        request.headers['Authorization'] = f"Bearer {self.__token}"
        return request


class ServiceNowHMACAuth(AuthBase):
    """
    Standard HMAC auth for Now which uses the out of box default Script Include
    """
    def __init__(self, shared_secret: bytes, key_id: str = None):
        self.__secret = shared_secret
        self._key_id = key_id
        try:
            import hmac, hashlib
            self.hmac = hmac
            self.algo = hashlib.sha256
        except:
            raise AuthenticationException('Install dependency hmac')

    def __call__(self, request):
        # only need to sign the body, which i disagree with
        signature = self.hmac.new(self.__secret, request.body, self.ago)
        encoded_sig = base64.standard_b64encode(signature.digest()).decode()

        header_value = f"signature={encoded_sig}"
        if self._key_id:
            header_value = f"keyId={self._key_id},{header_value}"
        request.headers['x-sn-hmac-signature-256'] = header_value
        return request


class ServiceAPIKey(AuthBase):
    """
    Standard API Key Authentication
    """
    def __init__(self, api_key: str):
        self.__api_key = api_key

    def __call__(self, request):
        request.headers['x-snc-api-key'] = self.__api_key
        return request

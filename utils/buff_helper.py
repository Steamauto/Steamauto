from typing import Dict

import requests


def parse_openid_params(self, response: str) -> Dict[str, str]:
    """
    page = html.document_fromstring(response)
    params = {
        'action': '',
        'openid.mode': '',
        'openidparams': '',
        'nonce': '',
    }
    for key in params:
        params[key] = page.cssselect(f'input[name="{key}"]')[0].attrib['value']
    return params
    """
    return {}


def get_openid_params(self) -> Dict[str, str]:
    response = requests.get('https://buff.163.com/account/login/steam?back_url=/', allow_redirects=False)
    return self.parse_openid_params(response.text)

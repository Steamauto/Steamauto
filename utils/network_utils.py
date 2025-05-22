import random
import re

class accelerator:
    def __call__(self, r):
        domain_list = [
            "steamcommunity-a.akamaihd.net",
        ]
        match = re.search(r"(https?://)([^/\s]+)", r.url)
        if match:
            domain = match.group(2)
            r.headers["Host"] = domain
            r.url = re.sub(r"(https?://)([^/\s]+)(.*)", r"\1" + random.choice(domain_list) + r"\3", r.url)
        return r

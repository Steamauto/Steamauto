import base64
from Crypto.Cipher import AES, PKCS1_v1_5
from Crypto.PublicKey import RSA
from Crypto.Util.Padding import pad, unpad

class UUApiCrypt:
    def __init__(self, aes_key):
        self.aes_key = aes_key.encode("utf-8")
        self.public_key = """-----BEGIN PUBLIC KEY-----\nMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAv9BDdhCDahZNFuJeesx3gzoQfD7pE0AeWiNBZlc21ph6kU9zd58X/1warV3C1VIX0vMAmhOcj5u86i+L2Lb2V68dX2Nb70MIDeW6Ibe8d0nF8D30tPsM7kaAyvxkY6ECM6RHGNhV4RrzkHmf5DeR9bybQGE0A9jcjuxszD1wsW/n19eeom7MroHqlRorp5LLNR8bSbmhTw6M/RQ/Fm3lKjKcvs1QNVyBNimrbD+ZVPE/KHSZLQ1jdF6tppvFnGxgJU9NFmxGFU0hx6cZiQHkhOQfGDFkElxgtj8gFJ1narTwYbvfe5nGSiznv/EUJSjTHxzX1TEkex0+5j4vSANt1QIDAQAB\n-----END PUBLIC KEY-----"""

    def get_encrypted_aes_key(self):
        public_key = RSA.import_key(self.public_key)
        cipher_rsa = PKCS1_v1_5.new(public_key)
        encrypted_aes_key = cipher_rsa.encrypt(self.aes_key)
        encrypted_aes_key_base64 = base64.b64encode(encrypted_aes_key).decode("utf-8")
        return encrypted_aes_key_base64
    
    def uu_encrypt(self, content):
        cipher_aes = AES.new(self.aes_key, AES.MODE_ECB)
        content_bytes = content.encode("utf-8")
        encrypted_content = cipher_aes.encrypt(pad(content_bytes, AES.block_size))
        encrypted_base64 = base64.b64encode(encrypted_content).decode("utf-8")
        return encrypted_base64
    
    def uu_decrypt(self, encrypted_base64):
        cipher_aes = AES.new(self.aes_key, AES.MODE_ECB)
        encrypted_content = base64.b64decode(encrypted_base64)
        decrypted_content = unpad(cipher_aes.decrypt(encrypted_content), AES.block_size)
        return decrypted_content.decode("utf-8")
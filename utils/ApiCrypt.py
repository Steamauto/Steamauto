import base64
import os
from Crypto.Cipher import AES, PKCS1_OAEP
from Crypto.PublicKey import RSA


class ApiCrypt:
    def __init__(self):
        self.public_key = """-----BEGIN PUBLIC KEY-----
        MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEArF75iD8PXTT+B5nAnnhw
        qxg9I48t9uED7r6GuRcPYUZ0Ye3Vdvs71CVjuELyxALtj5cN+Pe1DwDSUAH1TF+9
        dS7769gcJaMMdgEB6vyssm9fnPKB4KXqbUHdMT1MF2tylemDlqfsfpkV91wtAhHf
        SkNtsQcPw4Juhn0IK+2xyvlm6HtXqFOkhial5T+miGBJk3snHfLPmQFsg/3EuHFM
        tBzoLX29C46SNv/W33dwOk3mgIP1SMy4TLmm8CuyNiCuHPum53Q3RXSGrpR2nJps
        4ICIWb0P3VZmPhCrDK1iWwwtVGj9jDkCT2zh+B18j26vfTkBDdac5s4sw739uAha
        bH56BQflowPICHVWtptCEnORewxo/FDhFUtn4sjiQswgnTHJ6F/q0vwegRRsx0AT
        f3SvpksR6dZuUqHzISthooQ/68PrJ8VaKfT17u43pif08/bFkZAkYdLev4Mk0SlZ
        YOqpRoif+7Pi0yObTZ0bgpCwDb1kgAmqCHi9pFPS/LUMVqSqMa4maxAX2A8a/cbl
        CJbjBHLn0zrZn3YW4hKlaVvGFG/Mmag+ALV5xII0y6JSoqdxlxpyhEmbOi/GCFMw
        0Mn6lyvYDCvYVwS7UqLMw7NU3WXhbNUh8DgBSb5jo4yY9E42d24JiumZulzkSdgy
        OSkVea8JGUUD8PliMtRJOQkCAwEAAQ==
        -----END PUBLIC KEY-----
        """

    def encrypt(self, content):
        # Generate a random AES key
        aes_key = os.urandom(16)
        iv = os.urandom(16)

        # Load the public key
        public_key = RSA.import_key(self.public_key)

        # Encrypt the AES key with RSA
        cipher_rsa = PKCS1_OAEP.new(public_key)
        encrypted_aes_key = cipher_rsa.encrypt(aes_key)

        # Encrypt the content with AES
        cipher_aes = AES.new(aes_key, AES.MODE_CBC, iv)
        content_bytes = content.encode('utf-8')
        # Pad the content to match the block size
        content_bytes += b'\0' * (16 - len(content_bytes) % 16)
        encrypted_content = cipher_aes.encrypt(content_bytes)

        # Concatenate the encrypted AES key, IV, and content
        encrypted_data = encrypted_aes_key + iv + encrypted_content

        # Encode the encrypted data in base64
        encrypted_base64 = base64.b64encode(encrypted_data).decode('utf-8')

        return encrypted_base64


if __name__ == "__main__":
    api_crypt = ApiCrypt()
    encrypted_data = api_crypt.encrypt("Hello, world!")
    print("Encrypted data:", encrypted_data)

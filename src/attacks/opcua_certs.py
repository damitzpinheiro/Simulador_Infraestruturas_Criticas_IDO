"""
Geracao de certificados self-signed para o laboratorio de ataque OPC-UA.

O OPC-UA seguro (SignAndEncrypt) autentica cada ponta por certificado X.509.
Aqui geramos certificados de laboratorio para tres papeis:
  - server:  o servidor OPC-UA legitimo;
  - proxy:   o servidor OPC-UA malicioso (o MITM se apresenta com ESTE cert);
  - client:  o cliente/vitima.

O ponto do experimento e justamente o certificado do proxy ser DIFERENTE do
certificado do servidor real: um cliente que valida a identidade rejeita o
proxy; um cliente em "trust all" o aceita. Uso academico/defensivo.
"""

import asyncio
from pathlib import Path

from asyncua.crypto.cert_gen import setup_self_signed_certificate
from cryptography.x509.oid import ExtendedKeyUsageOID


async def ensure_cert(cert_dir: Path, nome: str, app_uri: str) -> tuple:
    """Gera (se ainda nao existir) o par cert/chave para um papel. Retorna
    (cert_path, key_path)."""
    cert_dir.mkdir(parents=True, exist_ok=True)
    cert_path = cert_dir / f"{nome}_cert.der"
    key_path = cert_dir / f"{nome}_key.pem"
    if cert_path.exists() and key_path.exists():
        return cert_path, key_path

    await setup_self_signed_certificate(
        key_path,
        cert_path,
        app_uri,
        "127.0.0.1",
        [ExtendedKeyUsageOID.CLIENT_AUTH, ExtendedKeyUsageOID.SERVER_AUTH],
        {
            "countryName": "BR",
            "stateOrProvinceName": "RJ",
            "localityName": "Rio de Janeiro",
            "organizationName": f"PFC-{nome}",
            "commonName": f"PFC {nome}",
        },
    )
    return cert_path, key_path


async def gerar_todos(cert_dir: Path) -> dict:
    """Gera os certificados dos tres papeis e devolve os caminhos."""
    papeis = {
        "server": "urn:pfc:opcua:server",
        "proxy": "urn:pfc:opcua:proxy",
        "client": "urn:pfc:opcua:client",
    }
    out = {}
    for nome, uri in papeis.items():
        cert, key = await ensure_cert(cert_dir, nome, uri)
        out[nome] = {"cert": cert, "key": key, "uri": uri}
    return out


if __name__ == "__main__":
    import sys
    d = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("certs")
    certs = asyncio.run(gerar_todos(d))
    for nome, info in certs.items():
        print(f"{nome}: {info['cert'].name}, {info['key'].name}")

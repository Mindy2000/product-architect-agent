"""Credential-free, DNS-pinned HTTPS worker. No dotenv, cookies, proxies or redirects."""
import base64
import http.client
import ipaddress
import json
import re
import socket
import ssl
import sys
from urllib.parse import urlsplit,urlunsplit,quote,parse_qsl
import truststore

MAX_BYTES=1024*1024


def normalize(value):
    if not isinstance(value,str) or not 1<=len(value)<=2048 or re.search(r'[\x00-\x20\x7f\\]',value):raise ValueError('url_invalid')
    parsed=urlsplit(value)
    if parsed.scheme!='https' or parsed.username or parsed.password or parsed.port not in {None,443} or not parsed.hostname:raise ValueError('https_only')
    host=parsed.hostname.encode('idna').decode('ascii').lower().rstrip('.')
    if len(host)>253 or not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}',host) or host.endswith(('.local','.localhost','.internal','.lan','.onion')):raise ValueError('host_invalid')
    for key,item in parse_qsl(parsed.query,keep_blank_values=True,max_num_fields=30):
        if re.search(r'(?i)(?:token|(?:api|access)[_-]?key|password|secret|signature|authorization|session|cookie|credential|^sig$|^key$|^auth$|^code$)',key) or re.search(r'\bsk-[A-Za-z0-9_-]{12,}',item):raise ValueError('credentials_forbidden')
    if re.search(r'(?i)/(?:login|signin|sign-in|oauth|logout)(?:/|$)',parsed.path):raise ValueError('login_forbidden')
    path=quote(parsed.path or '/',safe='/%:@!$&\'()*+,;=-._~')
    query=quote(parsed.query,safe='%=&:+,;/?!$\'()*-._~')
    normalized=urlunsplit(('https',host,path,query,''))
    if len(normalized)>2048:raise ValueError('url_invalid')
    return normalized


def public(address):
    value=ipaddress.ip_address(address.split('%')[0])
    return value.is_global and not any((value.is_multicast,value.is_reserved,value.is_loopback,value.is_link_local,value.is_unspecified))


class Pinned(http.client.HTTPSConnection):
    def __init__(self,host,info,timeout):
        self.info=info
        context=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname=True;context.verify_mode=ssl.CERT_REQUIRED
        super().__init__(host,443,timeout=timeout,context=context)
    def connect(self):
        family,kind,protocol,_,address=self.info
        raw=socket.socket(family,kind,protocol);raw.settimeout(self.timeout)
        try:
            raw.connect(address)
            if not public(raw.getpeername()[0]):raise ValueError('private_address')
            self.sock=self._context.wrap_socket(raw,server_hostname=self.host)
        except BaseException:raw.close();raise


def fetch(value):
    url=normalize(value['url']);parsed=urlsplit(url)
    infos=socket.getaddrinfo(parsed.hostname,443,type=socket.SOCK_STREAM)
    if not infos or len(infos)>32 or any(not public(info[4][0]) for info in infos):raise ValueError('private_address')
    selected=sorted(infos,key=lambda info:info[0]!=socket.AF_INET)[0]
    connection=Pinned(parsed.hostname,selected,min(10,max(1,float(value.get('timeout',8)))))
    try:
        target=parsed.path+('?' + parsed.query if parsed.query else '')
        connection.request('GET',target,headers={'User-Agent':'ProductArchitectAgent/0.1 (local user-triggered public research)','Accept':'text/html,text/plain,application/json;q=0.8','Accept-Encoding':'identity','Connection':'close'})
        response=connection.getresponse();status=response.status
        result={'status':status,'content_type':response.getheader('Content-Type',''),'location':response.getheader('Location',''),'url':url}
        if status in {301,302,303,307,308}:return result
        if response.getheader('Content-Encoding','identity').lower() not in {'','identity'}:raise ValueError('encoding_unsupported')
        length=response.getheader('Content-Length')
        if length and int(length)>MAX_BYTES:raise ValueError('body_limit')
        content=bytearray()
        while True:
            part=response.read1(min(65536,MAX_BYTES+1-len(content)))
            if not part:break
            content.extend(part)
            if len(content)>MAX_BYTES:raise ValueError('body_limit')
        result['body']=base64.b64encode(content).decode()
        return result
    finally:connection.close()


if __name__=='__main__':
    try:
        raw=sys.stdin.buffer.read(8193)
        if len(raw)>8192:raise ValueError('input_limit')
        print(json.dumps(fetch(json.loads(raw))))
    except Exception as error:
        code=str(error) if isinstance(error,ValueError) else 'network_unavailable'
        allowed={'url_invalid','https_only','host_invalid','credentials_forbidden','login_forbidden','private_address','body_limit','encoding_unsupported','input_limit'}
        print(json.dumps({'error':code if code in allowed else 'network_unavailable'}));sys.exit(1)

"""jobtpl.py: load the shared LAVA templates with {PLACEHOLDER}s filled.

Every {NAME} in a template is replaced by the environment variable NAME
when it is set (or by an explicit value passed in); unset placeholders
stay in the output so the job can be finished by hand. ARTIFACT_HEADER
("Name: value"), when set, is added as the HTTP header of every deploy
URL (for artifact stores that need one)."""
import os
import re

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))


def fill(text, values=None):
    v = dict(os.environ)
    v.update(values or {})
    return re.sub(r'\{([A-Z][A-Z0-9_]*)\}', lambda m: v.get(m.group(1), m.group(0)), text)


def load(name, values=None):
    return yaml.safe_load(fill(open(os.path.join(HERE, name)).read(), values))


def headers():
    h = os.environ.get('ARTIFACT_HEADER')
    if not h:
        return None
    k, _, val = h.partition(':')
    return {k.strip(): val.strip()}


def add_url(d, url):
    """Set d['url'] (and the artifact header, if any)."""
    d['url'] = fill(url)
    h = headers()
    if h:
        d['headers'] = h


def backstop(device, tag):
    b = load('backstop.yaml', {'DEVICE': device})
    h = headers()
    if h:
        b['actions'][0]['deploy']['qcomflash']['headers'] = h
    b['job_name'] = 'eliza-evk backstop %s (stock restore)' % tag
    b['metadata'] = {'purpose': 'stock restore backstop after %s' % tag}
    return b


def dump(obj, path):
    with open(path, 'w') as f:
        yaml.safe_dump(obj, f, sort_keys=False, width=100000)

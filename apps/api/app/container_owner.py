"""Labels owned by this API process; never remove containers owned by a live process."""
import hashlib,os
from .runner import ROOT
PROJECT=hashlib.sha256(str(ROOT.resolve()).encode()).hexdigest()[:20]
LABELS=['--label','pa.app=product-architect','--label','pa.project='+PROJECT,'--label','pa.owner='+str(os.getpid())]

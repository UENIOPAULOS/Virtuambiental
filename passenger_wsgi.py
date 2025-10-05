import os, sys
curdir = os.path.dirname(__file__)
if curdir not in sys.path:
    sys.path.insert(0, curdir)
from app import app as application

import os, sys
from PIL import ImageGrab
ImageGrab.grab(xdisplay=os.environ["DISPLAY"]).save(sys.argv[1])

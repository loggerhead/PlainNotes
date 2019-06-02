import sublime, sublime_plugin
import webbrowser
import urllib.request
import base64
import io
import os
import struct
from collections import defaultdict

ST3072 = int(sublime.version()) >= 3072
PHANTOMS = defaultdict(set)

def is_enabled_for_view(view):
    valid_syntax = [
        'Note.tmLanguage', 'Note.sublime-syntax',
    ]
    syntax = view.settings().get("syntax")
    return any(syntax.endswith(s) or 'markdown' in syntax.lower() for s in valid_syntax)

def getPathType(path):
    if path.startswith('http://') or path.startswith('https://'):
        return 'http'
    elif path.startswith('/'):
        # absolute path
        return 'apath'
    else:
        # relative path
        return 'rpath'

def readImage(view, path):
    data = None
    path_type = getPathType(path)
    if path_type == 'http':
        req = urllib.request.Request(path)
        r = urllib.request.urlopen(req)
        data = r.read()
    else:
        if path_type == 'rpath':
            basedir = os.path.split(view.file_name())[0]
            path = os.path.join(basedir, path)
        data = open(path, 'rb').read()
    return data

def getPreviewDimensions(w, h, max_w, max_h):
    margin = 100
    ratio = w / (h * 1.0)
    if max_w >= max_h:
        width = (max_w - margin)
        height = width / ratio
    else:
        height = (max_h - margin)
        width = height * ratio
    return (width, height)

def getImageInfo(data):
    data = data
    size = len(data)
    height = -1
    width = -1
    content_type = ''

    # handle GIFs
    if (size >= 10) and data[:6] in (b'GIF87a', b'GIF89a'):
        # Check to see if content_type is correct
        content_type = 'image/gif'
        w, h = struct.unpack(b"<HH", data[6:10])
        width = int(w)
        height = int(h)

    # See PNG 2. Edition spec (http://www.w3.org/TR/PNG/)
    # Bytes 0-7 are below, 4-byte chunk length, then 'IHDR'
    # and finally the 4-byte width, height
    elif ((size >= 24) and data.startswith(b'\211PNG\r\n\032\n') and
          (data[12:16] == b'IHDR')):
        content_type = 'image/png'
        w, h = struct.unpack(b">LL", data[16:24])
        width = int(w)
        height = int(h)

    # Maybe this is for an older PNG version.
    elif (size >= 16) and data.startswith(b'\211PNG\r\n\032\n'):
        # Check to see if we have the right content type
        content_type = 'image/png'
        w, h = struct.unpack(b">LL", data[8:16])
        width = int(w)
        height = int(h)

    # handle JPEGs
    elif (size >= 2) and data.startswith(b'\377\330'):
        content_type = 'image/jpeg'
        jpeg = io.BytesIO(data)
        jpeg.read(2)
        b = jpeg.read(1)
        try:
            while (b and ord(b) != 0xDA):
                while (ord(b) != 0xFF): b = jpeg.read(1)
                while (ord(b) == 0xFF): b = jpeg.read(1)
                if (ord(b) >= 0xC0 and ord(b) <= 0xC3):
                    jpeg.read(3)
                    h, w = struct.unpack(b">HH", jpeg.read(4))
                    break
                else:
                    jpeg.read(int(struct.unpack(b">H", jpeg.read(2))[0])-2)
                b = jpeg.read(1)
            width = int(w)
            height = int(h)
        except struct.error:
            pass
        except ValueError:
            pass

    return content_type, width, height

def genImageHtml(view, data):
    b64 = base64.b64encode(data)
    mime, w, h = getImageInfo(data)
    max_w, max_h = view.viewport_extent()
    win_w, win_h = getPreviewDimensions(w, h, max_w, max_h)
    html = '''
        <style>body, html {{margin: 0; padding: 0}}</style>
        <img width="{width}" height="{height}" src="data:image/png;base64,{data}">
    '''.format(width=win_w, height=win_h, data=b64.decode('utf-8'))
    return html, win_w, win_h

class NoteOpenUrlCommand(sublime_plugin.TextCommand):

    def run(self, edit):
        v = self.view
        s = v.sel()[0]
        link_region = v.extract_scope(s.a)
        path = v.substr(link_region)
        path_type = getPathType(path)

        if path_type == 'http':
            webbrowser.open_new_tab(url)
        else:
            if path_type == 'apath':
                basedir = os.path.split(view.file_name())[0]
                path = os.path.join(basedir, path)
            sublime.active_window().open_file(path, sublime.ENCODED_POSITION)

    def is_enabled(self):
        return is_enabled_for_view(self.view)

# ref: https://github.com/renerocksai/sublime_zk/blob/master/sublime_zk.py#L298-L370
class NotePreviewOrHideAllImageCommand(sublime_plugin.TextCommand):

    def run(self, edit):
        v = self.view
        img_regs = v.find_by_selector('markup.underline.link.image.markdown')
        is_show = len(PHANTOMS[v.id()]) == 0

        for region in img_regs:
            rid = str(v.line(region))
            if is_show:
                path = v.substr(region)
                data = readImage(v, path)
                html, w, h = genImageHtml(v, data)
                v.erase_phantoms(rid)
                v.add_phantom(rid, region, html, sublime.LAYOUT_BLOCK)
                PHANTOMS[v.id()].add(rid)
            else:
                v.erase_phantoms(rid)
                PHANTOMS[v.id()].discard(rid)

    def is_enabled(self):
        return is_enabled_for_view(self.view)

class NotePreviewOrHideImageCommand(sublime_plugin.TextCommand):

    def run(self, edit):
        v = self.view
        s = v.sel()[0]
        region = v.extract_scope(s.a)
        rid = str(v.line(region))

        if rid in PHANTOMS[v.id()]:
            v.erase_phantoms(rid)
            PHANTOMS[v.id()].discard(rid)
        else:
            path = v.substr(region).strip('()')
            data = readImage(v, path)
            html, w, h = genImageHtml(v, data)
            v.erase_phantoms(rid)
            v.add_phantom(rid, region, html, sublime.LAYOUT_BLOCK)
            PHANTOMS[v.id()].add(rid)

    def is_enabled(self):
        return is_enabled_for_view(self.view)

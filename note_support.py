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

def unescape_path(path):
    return path.replace('\\', '')

def warningIfPathNotExists(path):
    is_exists = os.path.exists(path)
    if not is_exists:
        sublime.error_message("not found '%s'" % path)
    return is_exists

def readImage(view, path):
    data = None
    path_type = getPathType(path)
    # if exception raised, let it go
    if path_type == 'http':
        view.set_status("loading_image", "loading image '%s'..." % path)
        req = urllib.request.Request(path)
        r = urllib.request.urlopen(req)
        data = r.read()
    else:
        if path_type == 'rpath':
            basedir = os.path.split(view.file_name())[0]
            path = os.path.join(basedir, path)
        path = unescape_path(path)
        if not warningIfPathNotExists(path):
            return data
        data = open(path, 'rb').read()
    view.erase_status("loading_image")
    return data

def getPreviewDimensions(w, h, max_w, max_h):
    if w <= max_w:
        return (w, h)

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
            webbrowser.open_new_tab(path)
        else:
            if path_type == 'rpath':
                basedir = os.path.split(v.file_name())[0]
                path = os.path.join(basedir, path)
            path = unescape_path(path)
            if not warningIfPathNotExists(path):
                return
            sublime.active_window().open_file(path, sublime.ENCODED_POSITION)

    def is_enabled(self):
        return is_enabled_for_view(self.view)

# preview image base handler
class PreviewImageBaseHandler(sublime_plugin.TextCommand):
    def get_view_id(self, view, region):
        return str(view.line(region))

    def preview(self, view, region):
        rid = self.get_view_id(view, region)
        path = view.substr(region).strip('()')
        data = readImage(view, path)
        html, w, h = genImageHtml(view, data)
        view.erase_phantoms(rid)
        view.add_phantom(rid, region, html, sublime.LAYOUT_BLOCK)
        PHANTOMS[view.id()].add(rid)

    def hide(self, view, region):
        rid = self.get_view_id(view, region)
        view.erase_phantoms(rid)
        PHANTOMS[view.id()].discard(rid)

# ref: https://github.com/renerocksai/sublime_zk/blob/master/sublime_zk.py#L298-L370
class NotePreviewOrHideAllImageCommand(PreviewImageBaseHandler):

    def run(self, edit):
        v = self.view
        img_regs = v.find_by_selector('markup.underline.link.image.markdown')
        is_show = (len(PHANTOMS[v.id()]) == 0)

        for region in img_regs:
            if is_show:
                sublime.set_timeout_async(lambda: self.preview(v, region))
            else:
                self.hide(v, region)

    def is_enabled(self):
        return is_enabled_for_view(self.view)

class NotePreviewOrHideImageCommand(PreviewImageBaseHandler):

    def run(self, edit):
        v = self.view
        region = v.extract_scope(v.sel()[0].a)
        rid = self.get_view_id(v, region)
        is_show = (rid not in PHANTOMS[v.id()])
        if is_show:
            sublime.set_timeout_async(lambda: self.preview(v, region))
        else:
            self.hide(v, region)

    def is_enabled(self):
        return is_enabled_for_view(self.view)

# https://www.sublimetext.com/docs/3/api_reference.html
import sublime, sublime_plugin
import webbrowser
import urllib.request
import base64
import io
import os
import re
import socket
import struct
import threading
from collections import defaultdict

ST3072 = int(sublime.version()) >= 3072
LOADING_IMAGE_TIMEOUT = 5
PHANTOMS = defaultdict(set)


def is_enabled_for_view(view):
    valid_syntax = [
        'Note.tmLanguage', 'Note.sublime-syntax',
    ]
    syntax = view.settings().get("syntax")
    return any(syntax.endswith(s) or 'markdown' in syntax.lower() for s in valid_syntax)

def is_image_suffix(path):
    suffix = os.path.splitext(path)[-1].lower().strip('.')
    return suffix in ['png', 'jpg', 'jpeg', 'gif']

def get_path_type(path):
    if path.startswith('http://') or path.startswith('https://'):
        return 'http'
    elif path.startswith('/'):
        # absolute path
        return 'apath'
    else:
        # relative path
        return 'rpath'

# remove '\' char from path
def unescape_path(path):
    return path.replace('\\', '')

def alert_if_path_not_exists(path):
    is_exists = os.path.exists(path)
    if not is_exists:
        sublime.error_message("not found '%s'" % path)
    return is_exists

def read_image(view, path):
    if not is_image_suffix(path):
        print("'%s' is not image" % path)
        return
    # cache all loaded images
    cached_datas = getattr(view, "__cached__", {})
    # read image from cache
    if path in cached_datas:
        return cached_datas.get(path)

    # if any exception raised, let it go
    data = None
    path_type = get_path_type(path)
    # show loading message in status bar
    view.set_status("loading_image", "loading image '%s'..." % path)

    # read image data from HTTP
    try:
        if path_type == 'http':
            req = urllib.request.Request(path)
            r = urllib.request.urlopen(req, timeout=LOADING_IMAGE_TIMEOUT)
            data = r.read()
            # cache data
            cached_datas[path] = data
            setattr(view, "__cached__", cached_datas)
        # read image data from local file
        else:
            if path_type == 'rpath':
                basedir = os.path.split(view.file_name())[0]
                path = os.path.join(basedir, path)
            # remove '\' char from path
            path = unescape_path(path)
            if not alert_if_path_not_exists(path):
                return None
            data = open(path, 'rb').read()
    except socket.timeout:
        sublime.error_message("open image '%s' timeout!" % path)

    # remove loading message in status bar
    view.erase_status("loading_image")
    return data

def get_preview_dimensions(w, h, max_w, max_h):
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

def get_image_info(data):
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

def gen_image_html(view, data):
    b64 = base64.b64encode(data)
    mime, w, h = get_image_info(data)
    max_w, max_h = view.viewport_extent()
    win_w, win_h = get_preview_dimensions(w, h, max_w, max_h)
    html = '''
        <style>body, html {{margin: 0; padding: 0}}</style>
        <img width="{width}" height="{height}" src="data:image/png;base64,{data}">
    '''.format(width=win_w, height=win_h, data=b64.decode('utf-8'))
    return html, win_w, win_h


# preview image base handler
class PreviewImageBaseHandler(sublime_plugin.TextCommand):

    # return region of scope
    def get_selection_belong_scope(self):
        return self.view.extract_scope(self.view.sel()[0].a)

    def get_view_id(self, region):
        return str(self.view.line(region))

    def preview(self, region):
        def doPreview():
            # get image path from region
            path = self.view.substr(region)
            m = re.match('.*\[.*\]\((.*)\).*', path)
            if m:
                path = m.group(1)
            # read image data
            data = read_image(self.view, path)
            if not data:
                return
            html, w, h = gen_image_html(self.view, data)
            rid = self.get_view_id(region)
            self.view.erase_phantoms(rid)
            self.view.add_phantom(rid, region, html, sublime.LAYOUT_BLOCK)
            PHANTOMS[self.view.id()].add(rid)
        # async load image
        t = threading.Thread(target=doPreview)
        t.start()
        return t

    def hide(self, region):
        rid = self.get_view_id(region)
        self.view.erase_phantoms(rid)
        PHANTOMS[self.view.id()].discard(rid)

# ref: https://github.com/renerocksai/sublime_zk/blob/master/sublime_zk.py#L298-L370
class NotePreviewOrHideAllImageCommand(PreviewImageBaseHandler):

    def run(self, edit):
        img_regs = self.view.find_by_selector('meta.image.inline.markdown')
        link_regs = self.view.find_by_selector('meta.link.inline.markdown')
        regs = img_regs + link_regs
        self.view.set_status("loading_all", "loading all %d images..." % len(regs))

        # all preview threads
        tt = []
        is_show = (len(PHANTOMS[self.view.id()]) == 0)
        for region in regs:
            if is_show:
                tt.append(self.preview(region))
            else:
                self.hide(region)

        def wait_all_preview_threads():
            for t in tt:
                t.join()
                self.view.set_status("loading_all", "loading all images finished")
        sublime.set_timeout_async(wait_all_preview_threads)

    def is_enabled(self):
        return is_enabled_for_view(self.view)

class NoteOpenCommand(PreviewImageBaseHandler):

    def open_image(self, region):
        rid = self.get_view_id(region)
        is_show = (rid not in PHANTOMS[self.view.id()])
        if is_show:
            self.preview(region)
        else:
            self.hide(region)

    def open_web(self, path):
        webbrowser.open_new_tab(path)

    # open local file with sublime
    def open_file(self, path):
        path = unescape_path(path)
        if not alert_if_path_not_exists(path):
            return
        sublime.active_window().open_file(path, sublime.ENCODED_POSITION)

    def run(self, edit):
        region = self.get_selection_belong_scope()
        path = self.view.substr(region)
        path_type = get_path_type(path)

        if is_image_suffix(path):
            self.open_image(region)
        elif path_type == 'http':
            self.open_web(path)
        else:
            if path_type == 'rpath':
                basedir = os.path.split(self.view.file_name())[0]
                path = os.path.join(basedir, path)
            self.open_file(path)

    def is_enabled(self):
        return is_enabled_for_view(self.view)

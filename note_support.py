import sublime, sublime_plugin
import webbrowser
import urllib.request
import base64
import io
import os
import struct

ST3072 = int(sublime.version()) >= 3072

def is_enabled_for_view(view):
    valid_syntax = [
        'Note.tmLanguage', 'Note.sublime-syntax',
        'Markdown.sublime-syntax', 'Markdown GFM.sublime-syntax',
        'MultiMarkdown.sublime-syntax',
    ]
    syntax = view.settings().get("syntax")
    return any(syntax.endswith(s) for s in valid_syntax)

def readImage(view, path):
    data = None
    if path.startswith('http://') or path.startswith('https://'):
        req = urllib.request.Request(path)
        r = urllib.request.urlopen(req)
        data = r.read()
    elif path.startswith('/'):
        data = open(path, 'rb').read()
    else:
        basedir = os.path.split(view.file_name())[0]
        filepath = os.path.join(basedir, path)
        data = open(filepath, 'rb').read()
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
        url = v.substr(link_region)
        webbrowser.open_new_tab(url)

    def is_enabled(self):
        return is_enabled_for_view(self.view)

# ref: https://github.com/renerocksai/sublime_zk/blob/master/sublime_zk.py#L298-L370
class NoteShowAllImageCommand(sublime_plugin.TextCommand):

    def run(self, edit):
        v = self.view
        img_regs = v.find_by_selector('markup.underline.link.image.markdown')
        for region in img_regs:
            path = v.substr(region)
            data = readImage(v, path)
            html, w, h = genImageHtml(v, data)
            line_region = v.line(region)
            v.erase_phantoms(str(region))
            v.add_phantom(str(region), region, html, sublime.LAYOUT_BLOCK)

    def is_enabled(self):
        return is_enabled_for_view(self.view)

class NoteHideAllImageCommand(sublime_plugin.TextCommand):

    def run(self, edit):
        v = self.view
        img_regs = v.find_by_selector('markup.underline.link.image.markdown')
        for region in img_regs:
            v.erase_phantoms(str(region))

    def is_enabled(self):
        return is_enabled_for_view(self.view)

class NotePreviewImageCommand(sublime_plugin.TextCommand):

    def run(self, edit):
        v = self.view
        s = v.sel()[0]
        link_region = v.extract_scope(s.a)
        path = v.substr(link_region)
        data = readImage(v, path)
        html, w, h = genImageHtml(v, data)
        v.show_popup(html, max_width=w, max_height=h, location=link_region.a)

    def is_enabled(self):
        return is_enabled_for_view(self.view)

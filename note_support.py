# https://www.sublimetext.com/docs/3/api_reference.html
import sublime, sublime_plugin
import webbrowser
import base64
import io
import os
import re
import socket
import struct
import threading
import urllib
from urllib import request
from collections import defaultdict

ST3072 = int(sublime.version()) >= 3072
LOADING_IMAGE_TIMEOUT = 5
CACHE = defaultdict(lambda: defaultdict(dict))

def settings():
    return sublime.load_settings('Notes.sublime-settings')

def get_view_cache(view):
    return CACHE[view.id()]

def refresh_phantoms(phantoms):
    remains = []
    for _, phantom in phantoms.items():
        if not phantom.refresh():
            remains.append(phantom)
    phantoms.clear()
    for phantom in remains:
        phantoms[phantom.id()] = phantom

# base class
class PhantomContent(object):
    def load(self):
        raise NotImplementedError()

    def gen_html(self, max_w, max_h):
        raise NotImplementedError()

class Path(object):
    @staticmethod
    def remove_backslash(path):
        return path.replace('\\', '')

    @staticmethod
    def is_url(path):
        return path.startswith('http://') or path.startswith('https://')

    @staticmethod
    def is_image(path):
        suffix = os.path.splitext(path)[-1].lower().strip('.')
        return suffix in ['png', 'jpg', 'jpeg', 'gif']

    @staticmethod
    def get_abspath(view, path):
        basedir = os.path.split(os.path.abspath(view.file_name()))[0]
        abspath = os.path.join(basedir, path)
        if os.path.exists(abspath):
            return abspath
        else:
            return os.path.abspath(path)

    @staticmethod
    def get_abspath_if_not_url(view, path):
        path = Path.remove_backslash(path)
        if not Path.is_url(path):
            path = Path.get_abspath(view, path)
        return path


class Image(PhantomContent):
    def __init__(self, path):
        self.path = path
        self.data_in_base64 = None
        self.width = None
        self.height = None
        self.content_type = None

    def load(self):
        data = None
        try:
            # load from URL
            if Path.is_url(self.path):
                http_proxy = settings().get('http_proxy')
                https_proxy = settings().get('https_proxy')
                if http_proxy or https_proxy:
                    req = request.build_opener(request.ProxyHandler({
                        'http': http_proxy,
                        'https': https_proxy,
                    }))
                    r = req.open(self.path, timeout=LOADING_IMAGE_TIMEOUT)
                else:
                    req = request.Request(self.path)
                    r = request.urlopen(req, timeout=LOADING_IMAGE_TIMEOUT)
                data = r.read()
            # load from local file
            else:
                data = open(self.path, 'rb').read()
        except (socket.timeout, urllib.error.URLError):
            sublime.error_message("open image '%s' timeout!" % self.path)
        except (OSError, IOError):
            sublime.error_message("open image '%s' failed!" % self.path)
        except Exception as e:
            sublime.error_message("open image failed! %s" % e)
            raise e
        finally:
            if not data:
                return False
            self.content_type, self.width, self.height = self._get_info(data)
            self.data_in_base64 = base64.b64encode(data).decode('utf-8')
        return True

    # max_w, max_h = view.viewport_extent()
    def gen_html(self, max_w, max_h):
        win_w, win_h = self._get_preview_dimensions(max_w, max_h)
        html = '''<style>body, html {{margin: 0; padding: 0}}</style>
            <img width="{width}" height="{height}" src="data:{content_type};base64,{data}">
        '''.format(
            width=win_w,
            height=win_h,
            content_type=self.content_type,
            data=self.data_in_base64,
        )
        return html

    def _get_preview_dimensions(self, max_w, max_h):
        if self.width <= max_w:
            return (self.width, self.height)

        margin = 100
        ratio = self.width / (self.height * 1.0)
        if max_w >= max_h:
            width = (max_w - margin)
            height = width / ratio
        else:
            height = (max_h - margin)
            width = height * ratio
        return (width, height)

    def _get_info(self, data):
        size = len(data)
        height = -1
        width = -1
        content_type = ''

        if not data or not size:
            return None, None, None

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

    def __str__(self):
        return self.path

class Phantom(object):
    def __init__(self, view, region, phantom_content):
        self.view = view
        self.region = region
        self.rpos = (region.a, region.b, region.xpos)
        self.region_str = view.substr(region)
        self.phantom_content = phantom_content

    def preview(self, max_w, max_h):
        html = self.phantom_content.gen_html(max_w, max_h)
        self.view.add_phantom(self.id(), self.region, html, sublime.LAYOUT_BLOCK)

    def hide(self):
        self.view.erase_phantoms(self.id())

    def refresh(self, phantom_content=None):
        is_modified = (self.view.substr(self.region) != self.region_str)
        if is_modified:
            self.hide()
        if phantom_content:
            self.phantom_content = phantom_content
        return is_modified

    def id(self):
        return str((self.region_str, self.rpos))

    @classmethod
    def compute_phantom_id(cls, view, region):
        return Phantom(view, region, None).id()

    def __hash__(self):
        return self.id()

    def __eq__(self, o):
        return self.id() == o.id()

class _MarkdownBaseCommand(sublime_plugin.TextCommand):
    # preview image base handler
    def get_selection_point(self):
        return self.view.sel()[0].a

    # return region of scope
    def get_selection_belong_scope(self):
        return self.view.extract_scope(self.get_selection_point())

    def get_image_region_by_subregion(self, region):
        return self.view.line(region)

    def get_path_from_region(self, region):
        path = self.view.substr(region)
        m = re.match('.*\[.*\]\((.*)\).*', path)
        if m:
            path = m.group(1)
        path = re.sub("[\(\)]", "", path)
        return path

    @property
    def cache(self):
        return get_view_cache(self.view)

    @property
    def phantoms(self):
        return self.cache['__PHANTOMS__']

    @property
    def phantom_contents(self):
        return self.cache['__PHANTOM_CONTENTS__']

    def run(self, edit):
        raise NotImplementedError()

    def is_enabled(self):
        valid_syntax = [
            'Note.tmLanguage', 'Note.sublime-syntax',
        ]
        syntax = self.view.settings().get("syntax")
        return any(syntax.endswith(s) or 'markdown' in syntax.lower() for s in valid_syntax)

    # is_preview:
    #   None: preview or hide
    #   True: preview
    #   False: hide
    def preview_or_hide_image(self, path, region, is_preview=None):
        phantom_id = Phantom.compute_phantom_id(self.view, region)
        if is_preview is None:
            is_preview = (phantom_id not in self.phantoms)

        def preview():
            self.view.set_status(path, "loading image '%s'..." % path)
            if path in self.phantom_contents:
                image_content = self.phantom_contents[path]
            else:
                image_content = Image(path)
                if not image_content.load():
                    self.view.erase_status(path)
                    return
                self.phantom_contents[path] = image_content

            if phantom_id in self.phantoms:
                phantom = self.phantoms[phantom_id]
            else:
                phantom = Phantom(self.view, region, image_content)
                self.phantoms[phantom.id()] = phantom

            max_w, max_h = self.view.viewport_extent()
            phantom.preview(max_w, max_h)
            self.view.erase_status(path)

        def hide():
            if phantom_id not in self.phantoms:
                return
            phantom = self.phantoms[phantom_id]
            phantom.hide()
            self.phantoms.pop(phantom.id(), None)

        target_func = (preview if is_preview else hide)
        t = threading.Thread(target=target_func)
        t.start()
        return t

# ref: https://github.com/renerocksai/sublime_zk/blob/master/sublime_zk.py#L298-L370
class NotePreviewOrHideAllImageCommand(_MarkdownBaseCommand):
    def run(self, edit):
        refresh_phantoms(self.phantoms)
        # local variables
        img_regs = self.view.find_by_selector('meta.image.inline.markdown')
        link_regs = self.view.find_by_selector('meta.link.inline.markdown')
        is_preview = (len(self.phantoms) == 0)
        tt = []

        # preview or hide all images
        for region in img_regs:
            region = self.get_image_region_by_subregion(region)
            path = self.get_path_from_region(region)
            path = Path.get_abspath_if_not_url(self.view, path)
            t = self.preview_or_hide_image(path, region, is_preview)
            tt.append(t)

        # preview or hide all images for link scope
        for region in link_regs:
            region = self.get_image_region_by_subregion(region)
            path = self.get_path_from_region(region)
            path = Path.get_abspath_if_not_url(self.view, path)
            if not Path.is_image(path):
                continue
            t = self.preview_or_hide_image(path, region, is_preview)
            tt.append(t)

        # reset show position after loading
        def wait_all_preview_threads():
            current_point = self.get_selection_point()
            for t in tt:
                t.join()
            self.view.show_at_center(current_point)
        sublime.set_timeout_async(wait_all_preview_threads)

class NoteOpenCommand(_MarkdownBaseCommand):
    def open_image(self, path, region):
        self.preview_or_hide_image(path, region)

    def open_web(self, path, region):
        webbrowser.open_new_tab(path)

    def open_file(self, path, region):
        if not os.path.exists(path):
            raise FileNotFoundError()
        sublime.active_window().open_file(path, sublime.ENCODED_POSITION)

    def run(self, edit):
        region = self.get_image_region_by_subregion(self.get_selection_belong_scope())
        path = self.get_path_from_region(region)
        path = Path.get_abspath_if_not_url(self.view, path)

        try:
            if Path.is_url(path):
                if self.view.match_selector(region.a, 'meta.image.inline.markdown'):
                    self.open_image(path, region)
                else:
                    self.open_web(path, region)
            else:
                if Path.is_image(path):
                    self.open_image(path, region)
                else:
                    self.open_file(path, region)
        except:
            sublime.error_message("open '%s' failed!" % path)
            raise

class NotePasteImageCommand(_MarkdownBaseCommand):
    def run(self, edit):
        from PIL import ImageGrab

        dirpath, filename = os.path.split(os.path.abspath(self.view.file_name()))
        imgpath = os.path.join(dirpath, '%s.png' % filename)
        im = ImageGrab.grabclipboard()
        im.save(imgpath,'PNG')

class PhantomModifyEventHandler(sublime_plugin.ViewEventListener):
    @property
    def cache(self):
        return get_view_cache(self.view)

    @property
    def phantoms(self):
        return self.cache['__PHANTOMS__']

    def on_modified_async(self):
        refresh_phantoms(self.phantoms)

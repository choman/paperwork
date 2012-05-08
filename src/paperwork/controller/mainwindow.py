import os
import sys
import threading

import Image
import ImageColor
import gtk
import gettext
import gobject

from paperwork.controller.actions import connect_buttons
from paperwork.controller.actions import SimpleAction
from paperwork.controller.workers import Worker
from paperwork.model.doc import ScannedDoc
from paperwork.model.docsearch import DummyDocSearch
from paperwork.model.docsearch import DocSearch
from paperwork.util import image2pixbuf
from paperwork.util import load_uifile

_ = gettext.gettext


class WorkerDocIndexer(Worker):
    """
    Reindex all the documents
    """

    __gsignals__ = {
        'indexation-start' : (gobject.SIGNAL_RUN_LAST, gobject.TYPE_NONE, ()),
        'indexation-progression' : (gobject.SIGNAL_RUN_LAST, gobject.TYPE_NONE,
                                    (gobject.TYPE_FLOAT, gobject.TYPE_STRING)),
        'indexation-end' : (gobject.SIGNAL_RUN_LAST, gobject.TYPE_NONE, ()),
    }

    can_interrupt = True

    def __init__(self, main_window, config):
        Worker.__init__(self, "Document reindexation")
        self.__main_win = main_window
        self.__config = config

    def __cb_progress(self, progression, total, step, doc=None):
        """
        Update the main progress bar
        """
        txt = None
        if step == DocSearch.INDEX_STEP_READING:
            txt = _('Reading ...')
        elif step == DocSearch.INDEX_STEP_SORTING:
            txt = _('Sorting ...')
        else:
            assert()  # unknown progression type
            txt = ""
        if doc != None:
            txt += (" (%s)" % (doc.name))
        self.emit('indexation-progression', float(progression) / total, txt)
        if not self.can_run:
            raise StopIteration()

    def do(self):
        self.emit('indexation-start')
        try:
            docsearch = DocSearch(self.__config.workdir, self.__cb_progress)
            self.__main_win.docsearch = docsearch
        except StopIteration:
            print "Indexation interrupted"
        self.emit('indexation-end')

gobject.type_register(WorkerDocIndexer)


class WorkerThumbnailer(Worker):
    """
    Generate thumbnails
    """

    __gsignals__ = {
        'thumbnailing-start' : (gobject.SIGNAL_RUN_LAST, gobject.TYPE_NONE, ()),
        'thumbnailing-page-done': (gobject.SIGNAL_RUN_LAST, gobject.TYPE_NONE,
                                   (gobject.TYPE_INT, gobject.TYPE_PYOBJECT)),
        'thumbnailing-end' : (gobject.SIGNAL_RUN_LAST, gobject.TYPE_NONE, ()),
    }

    can_interrupt = True

    def __init__(self, main_window):
        Worker.__init__(self, "Thumbnailing")
        self.__main_win = main_window

    def do(self):
        self.emit('thumbnailing-start')
        for page_idx in range(0, self.__main_win.doc.nb_pages):
            page = self.__main_win.doc.pages[page_idx]
            img = page.get_thumbnail(150)
            pixbuf = image2pixbuf(img)
            if not self.can_run:
                return
            self.emit('thumbnailing-page-done', page_idx, pixbuf)
        self.emit('thumbnailing-end')


gobject.type_register(WorkerThumbnailer)


class WorkerImgBuilder(Worker):
    """
    Resize and paint on the page
    """
    __gsignals__ = {
        'img-building-start' : (gobject.SIGNAL_RUN_LAST, gobject.TYPE_NONE, ()),
        'img-building-result-pixbuf' : (gobject.SIGNAL_RUN_LAST, gobject.TYPE_NONE,
                                        (gobject.TYPE_PYOBJECT, )),
        'img-building-result-stock' : (gobject.SIGNAL_RUN_LAST, gobject.TYPE_NONE,
                                        (gobject.TYPE_STRING, )),
    }

    # even if it's not true, this process is not really long, so it doesn't
    # really matter
    can_interrupt = True

    def __init__(self, main_window):
        Worker.__init__(self, "Building page image")
        self.__main_win = main_window

    def __get_zoom_factor(self):
        el_idx = self.__main_win.lists['zoom_levels'][0].get_active()
        el_iter = self.__main_win.lists['zoom_levels'][1].get_iter(el_idx)
        return self.__main_win.lists['zoom_levels'][1].get_value(el_iter, 1)

    def __get_img_area_width(self):
        width = self.__main_win.scrollBars['img_area'][0].get_allocation().width
        # TODO(JFlesch): This is not a safe assumption:
        width -= 30
        return width

    def do(self):
        self.emit('img-building-start')

        if self.__main_win.page == None:
            self.emit('img-building-result-stock', gtk.STOCK_MISSING_IMAGE)
            return

        img = self.__main_win.page.img
        pixbuf = image2pixbuf(img)

        factor = self.__get_zoom_factor()
        print "Zoom: %f" % (factor)

        if factor == 0.0:
            wanted_width = self.__get_img_area_width()
            factor = float(wanted_width) / pixbuf.get_width()
            wanted_height = int(factor * pixbuf.get_height())
        else:
            wanted_width = int(factor * pixbuf.get_width())
            wanted_height = int(factor * pixbuf.get_height())
        pixbuf = pixbuf.scale_simple(wanted_width, wanted_height,
                                     gtk.gdk.INTERP_BILINEAR)

        self.emit('img-building-result-pixbuf', pixbuf)


gobject.type_register(WorkerImgBuilder)


class ActionNewDocument(SimpleAction):
    """
    Starts a new document.
    """
    def __init__(self, main_window, config):
        SimpleAction.__init__(self, "New document")
        self.__main_win = main_window
        self.__config = config

    def do(self):
        SimpleAction.do(self)
        if self.__main_win.workers['thumbnailer'].is_running:
            self.__main_win.workers['thumbnailer'].stop()
        if self.__main_win.workers['img_builder'].is_running:
            self.__main_win.workers['img_builder'].stop()
        self.__main_win.doc = ScannedDoc(self.__config.workdir)
        self.__main_win.page = None
        self.__main_win.thumbnails = []
        self.__main_win.refresh_page_list()
        self.__main_win.refresh_label_list()
        self.__main_win.workers['img_builder'].start()


class ActionOpenSelectedDocument(SimpleAction):
    """
    Starts a new document.
    """
    def __init__(self, main_window):
        SimpleAction.__init__(self, "Open selected document")
        self.__main_win = main_window

    def do(self):
        SimpleAction.do(self)

        selection_path = \
                self.__main_win.lists['matches'][0].get_selection().get_selected()
        if selection_path[1] == None:
            print "No document selected. Can't open"
            return
        doc = selection_path[0].get_value(selection_path[1], 1)

        print "Showing doc %s" % doc
        if self.__main_win.workers['thumbnailer'].is_running:
            self.__main_win.workers['thumbnailer'].stop()
        self.__main_win.doc = doc
        self.__main_win.page = self.__main_win.doc.pages[0]
        self.__main_win.refresh_page_list()
        self.__main_win.refresh_label_list()
        self.__main_win.workers['thumbnailer'].start()


class ActionStartWorker(SimpleAction):
    """
    Start a threaded job
    """
    def __init__(self, worker):
        SimpleAction.__init__(self, str(worker))
        self.__worker = worker

    def do(self):
        SimpleAction.do(self)
        self.__worker.start()


class ActionUpdateSearchResults(SimpleAction):
    """
    Update search results
    """
    def __init__(self, main_window):
        SimpleAction.__init__(self, "Update search results")
        self.__main_win = main_window
    
    def do(self):
        SimpleAction.do(self)
        self.__main_win.refresh_doc_list()


class ActionOpenSelectedPage(SimpleAction):
    def __init__(self, main_window):
        SimpleAction.__init__(self, "Show selected page")
        self.__main_win = main_window

    def do(self):
        SimpleAction.do(self)
        selection_path = self.__main_win.lists['pages'][0].get_selected_items()
        if len(selection_path) <= 0:
            return None
        # TODO(Jflesch): We should get the page number from the list content,
        # not from the position of the element in the list
        page_idx = selection_path[0][0]
        page = self.__main_win.doc.pages[page_idx]

        print "Showing page %s" % (str(page))
        if self.__main_win.workers['img_builder'].is_running:
            self.__main_win.workers['img_builder'].stop()
        self.__main_win.page = page
        self.__main_win.workers['img_builder'].start()


class ActionRefreshPage(SimpleAction):
    def __init__(self, main_window):
        SimpleAction.__init__(self, "Refresh current page")
        self.__main_win = main_window

    def do(self):
        SimpleAction.do(self)
        if self.__main_win.workers['img_builder'].is_running:
            self.__main_win.workers['img_builder'].stop()
        self.__main_win.workers['img_builder'].start()


class ActionQuit(SimpleAction):
    """
    Quit
    """
    def __init__(self, main_window):
        SimpleAction.__init__(self, "Quit")
        self.__main_win = main_window

    def do(self):
        SimpleAction.do(self)

        for worker in self.__main_win.workers.values():
            if worker.is_running and not worker.can_interrupt:
                print ("Sorry, can't quit. Another thread is still running and"
                       " can't be interrupted")
                return
        for worker in self.__main_win.workers.values():
            if worker.is_running:
                worker.stop()

        self.__main_win.window.destroy()
        gtk.main_quit()


class MainWindow(object):
    def __init__(self, config):
        img = Image.new("RGB", (150, 200), ImageColor.getrgb("#EEEEEE"))
        # TODO(Jflesch): Find a better default thumbnail
        self.default_thumbnail = image2pixbuf(img)
        del img

        widget_tree = load_uifile("mainwindow.glade")

        self.window = widget_tree.get_object("mainWindow")

        self.docsearch = DummyDocSearch()
        self.doc = None
        self.page = None

        self.lists = {
            'suggestions' : (
                widget_tree.get_object("entrySearch"),
                widget_tree.get_object("liststoreSuggestion")
            ),
            'matches' : (
                widget_tree.get_object("treeviewMatch"),
                widget_tree.get_object("liststoreMatch"),
            ),
            'pages' : (
                widget_tree.get_object("iconviewPage"),
                widget_tree.get_object("liststorePage"),
            ),
            'labels' : (
                widget_tree.get_object("treeviewLabel"),
                widget_tree.get_object("liststoreLabel"),
            ),
            'zoom_levels' : (
                widget_tree.get_object("comboboxZoom"),
                widget_tree.get_object("liststoreZoom"),
            ),
        }

        self.indicators = {
            'total_pages' : widget_tree.get_object("labelTotalPages"),
        }

        self.search_field = widget_tree.get_object("entrySearch")

        self.doc_browsing = {
            'matches' : widget_tree.get_object("treeviewMatch"),
            'pages' : widget_tree.get_object("iconviewPage"),
            'labels' : widget_tree.get_object("treeviewLabel"),
            'search' : self.search_field,
        }

        self.text_area = widget_tree.get_object("textviewPageTxt")
        self.img_area = widget_tree.get_object("imagePageImg")

        self.status = {
            'progress' : widget_tree.get_object("progressbar"),
            'text' : widget_tree.get_object("statusbar"),
        }

        self.popupMenus = {
            'labels' : (
                widget_tree.get_object("treeviewLabel"),
                widget_tree.get_object("popupmenuLabels")
            ),
            'matches' : (
                widget_tree.get_object("treeviewMatch"),
                widget_tree.get_object("popupmenuMatchs")
            ),
            'pages' : (
                widget_tree.get_object("iconviewPage"),
                widget_tree.get_object("popupmenuPages")
            )
        }

        self.scrollBars = {
            'img_area' : (widget_tree.get_object("scrolledwindowPageImg"),
                          self.img_area),
        }

        self.workers = {
            'reindex' : WorkerDocIndexer(self, config),
            'thumbnailer' : WorkerThumbnailer(self),
            'img_builder' : WorkerImgBuilder(self),
        }

        self.actions = {
            'new_doc' : (
                [
                    widget_tree.get_object("menuitemNew"),
                    widget_tree.get_object("toolbuttonNew"),
                ],
                ActionNewDocument(self, config),
            ),
            'open_doc' : (
                [
                    widget_tree.get_object("treeviewMatch"),
                ],
                ActionOpenSelectedDocument(self)
            ),
            'open_page' : (
                [
                    widget_tree.get_object("iconviewPage"),
                ],
                ActionOpenSelectedPage(self)
            ),
            'single_scan' : [
                widget_tree.get_object("menuitemScan"),
                widget_tree.get_object("imagemenuitemScanSingle"),
                widget_tree.get_object("toolbuttonScan"),
                widget_tree.get_object("menuitemScanSingle"),
            ],
            'multi_scan' : [
                widget_tree.get_object("imagemenuitemScanFeeder"),
                widget_tree.get_object("menuitemScanFeeder"),
            ],
            'print' : [
                widget_tree.get_object("menuitemPrint"),
                widget_tree.get_object("toolbuttonPrint"),
            ],
            'settings' : [
                widget_tree.get_object("menuitemSettings"),
                # TODO
            ],
            'quit' : (
                [
                    widget_tree.get_object("menuitemQuit"),
                    widget_tree.get_object("toolbuttonQuit"),
                ],
                ActionQuit(self),
            ),
            'add_label' : [
                widget_tree.get_object("buttonAddLabel"),
                # TODO
            ],
            'edit_label' : [
                widget_tree.get_object("menuitemEditLabel"),
                widget_tree.get_object("buttonEditLabel"),
            ],
            'del_label' : [
                widget_tree.get_object("menuitemDestroyLabel"),
                widget_tree.get_object("buttonDelLabel"),
            ],
            'open_doc_dir' : [
                widget_tree.get_object("menuitemOpenDocDir"),
                widget_tree.get_object("toolbuttonOpenDocDir"),
            ],
            'del_doc' : [
                widget_tree.get_object("menuitemDestroyDoc2"),
                # TODO
            ],
            'del_page' : [
                widget_tree.get_object("menuitemDestroyPage2"),
                # TODO
            ],
            'prev_page' : [
                widget_tree.get_object("toolbuttonPrevPage"),
            ],
            'next_page' : [
                widget_tree.get_object("toolbuttonNextPage"),
            ],
            'current_page' : [
                widget_tree.get_object("entryPageNb"),
            ],
            'zoom_levels' : (
                [
                    widget_tree.get_object("comboboxZoom"),
                ],
                ActionRefreshPage(self)
            ),
            'search' : (
                [
                    self.search_field,
                ],
                ActionUpdateSearchResults(self),
            ),

            # Advanced actions: having only 1 item to do them is fine
            'show_all_boxes' : [
                widget_tree.get_object("checkmenuitemShowAllBoxes"),
            ],
            'redo_ocr_doc': [
                widget_tree.get_object("menuitemReOcr"),
            ],
            'redo_ocr_all' : [
                widget_tree.get_object("menuitemReOcrAll"),
            ],
            'reindex' : (
                [
                    widget_tree.get_object("menuitemReindexAll"),
                ],
                ActionStartWorker(self.workers['reindex'])
            ),
            'about' : [
                widget_tree.get_object("menuitemAbout"),
            ],
        }

        connect_buttons(self.actions['new_doc'][0], self.actions['new_doc'][1])
        connect_buttons(self.actions['open_doc'][0],
                        self.actions['open_doc'][1])
        connect_buttons(self.actions['reindex'][0], self.actions['reindex'][1])
        connect_buttons(self.actions['quit'][0], self.actions['quit'][1])
        connect_buttons(self.actions['search'][0], self.actions['search'][1])
        connect_buttons(self.actions['open_page'][0],
                        self.actions['open_page'][1])
        connect_buttons(self.actions['zoom_levels'][0],
                        self.actions['zoom_levels'][1])

        self.workers['reindex'].connect('indexation-start', lambda indexer: \
            gobject.idle_add(self.__on_indexation_start))
        self.workers['reindex'].connect('indexation-progression',
            lambda indexer, progression, txt: \
                gobject.idle_add(self.set_progression, indexer,
                                 progression, txt))
        self.workers['reindex'].connect('indexation-end', lambda indexer: \
            gobject.idle_add(self.__on_indexation_end))

        self.workers['thumbnailer'].connect('thumbnailing-page-done',
                lambda thumbnailer, page_idx, thumbnail: \
                    gobject.idle_add(self.set_page_thumbnail, page_idx,
                                     thumbnail))

        self.workers['img_builder'].connect('img-building-start',
                lambda builder: \
                    gobject.idle_add(self.img_area.set_from_stock,
                        gtk.STOCK_EXECUTE, gtk.ICON_SIZE_DIALOG))
        self.workers['img_builder'].connect('img-building-result-pixbuf',
                lambda builder, img: \
                    gobject.idle_add(self.img_area.set_from_pixbuf, img))
        self.workers['img_builder'].connect('img-building-result-stock',
                lambda builder, img: \
                    gobject.idle_add(self.img_area.set_from_stock, img,
                                     gtk.ICON_SIZE_DIALOG))



        self.window.set_visible(True)

    def set_search_availability(self, enabled):
        for list_view in self.doc_browsing.values():
            list_view.set_sensitive(enabled)

    def set_mouse_cursor(self, cursor):
        self.window.window.set_cursor({
            "Normal" : None,
            "Busy" : gtk.gdk.Cursor(gtk.gdk.WATCH),
        }[cursor])

    def set_progression(self, src, progression, text):
        context_id = self.status['text'].get_context_id(str(src))
        self.status['text'].pop(context_id)
        if (text != None and text != ""):
            self.status['text'].push(context_id, text)
        self.status['progress'].set_fraction(progression)

    def __on_indexation_start(self):
        self.set_progression(self.workers['reindex'], 0.0, None)
        self.set_search_availability(False)
        self.set_mouse_cursor("Busy")

    def __on_indexation_end(self):
        self.set_progression(self.workers['reindex'], 0.0, None)
        self.set_search_availability(True)
        self.set_mouse_cursor("Normal")
        self.refresh_doc_list()
        self.refresh_label_list()

    def refresh_doc_list(self):
        """
        Update the suggestions list and the matching documents list based on
        the keywords typed by the user in the search field.
        """
        sentence = unicode(self.search_field.get_text())
        print "Search: %s" % (sentence.encode('ascii', 'replace'))

        suggestions = self.docsearch.find_suggestions(sentence)
        print "Got %d suggestions" % len(suggestions)
        self.lists['suggestions'][1].clear()
        for suggestion in suggestions:
            self.lists['suggestions'][1].append([suggestion])

        documents = self.docsearch.find_documents(sentence)
        print "Got %d documents" % len(documents)
        documents = reversed(documents)

        self.lists['matches'][1].clear()
        for doc in documents:
            labels = doc.labels
            final_str = doc.name
            nb_pages = doc.nb_pages
            if nb_pages > 1:
                final_str += (_("\n  %d pages") % (doc.nb_pages))
            if len(labels) > 0:
                final_str += ("\n  "
                        + "\n  ".join([x.get_html() for x in labels]))
            self.lists['matches'][1].append([final_str, doc])

    def refresh_page_list(self):
        """
        Reload and refresh the page list.
        Warning: Will set default thumbnail on all the pages
        """
        self.lists['pages'][1].clear()
        for page in self.doc.pages:
            self.lists['pages'][1].append([
                self.default_thumbnail,
                _('Page %d') % (page.page_nb + 1),
                page.page_nb
            ])
        self.indicators['total_pages'].set_text(
                _("/ %d") % (self.doc.nb_pages))

    def set_page_thumbnail(self, page_idx, thumbnail):
        print "Updating thumbnail %d" % (page_idx)
        line_iter = self.lists['pages'][1].get_iter(page_idx)
        self.lists['pages'][1].set_value(line_iter, 0, thumbnail)

    def refresh_label_list(self):
        """
        Reload and refresh the label list
        """
        self.lists['labels'][1].clear()
        labels = self.doc.labels
        for label in self.docsearch.label_list:
            self.lists['labels'][1].append([
                label.get_html(),
                (label in labels),
                label
            ])

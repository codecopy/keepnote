"""
    TakeNote
    Copyright Matt Rasmussen 2008
    
    Graphical User Interface for TakeNote Application
"""



# python imports
import sys, os, tempfile, re, subprocess, shlex, shutil, time, traceback

# pygtk imports
import pygtk
pygtk.require('2.0')
from gtk import gdk
import gtk.glade
import gobject
import pango

# takenote imports
import takenote
from takenote.gui import \
     get_resource, \
     get_resource_image, \
     get_resource_pixbuf, \
     get_accel_file
from takenote.notebook import \
     NoteBookError, \
     NoteBookVersionError
from takenote import notebook as notebooklib
import takenote.search
from takenote.gui import richtext
from takenote.gui.richtext import RichTextView, RichTextImage, RichTextError
from takenote.gui.richtext_tags import color_tuple_to_string
from takenote.gui.treeview import TakeNoteTreeView
from takenote.gui.listview import TakeNoteListView
from takenote.gui import \
    quote_filename, \
    screenshot_win, \
    dialog_app_options, \
    dialog_find, \
    dialog_drag_drop_test, \
    dialog_image_resize, \
    TakeNoteError
from takenote.gui.font_selector import FontSelector
from takenote.gui.editor import TakeNoteEditor
from takenote.gui.colortool import FgColorTool, BgColorTool



class FontUI (object):

    def __init__(self, widget, signal):
        self.widget = widget
        self.signal = signal


class TakeNoteWindow (gtk.Window):
    """Main windows for TakeNote"""

    def __init__(self, app):
        gtk.Window.__init__(self, gtk.WINDOW_TOPLEVEL)
        self.app = app
        self.notebook = None
        self.sel_nodes = []
        self.current_page = None
        self.maximized = False
        self.iconified = False
        self.queue_list_select = []
        self.ignore_view_mode = False

        self.font_ui_signals = []
        
        # init main window
        self.set_title(takenote.PROGRAM_NAME)
        self.set_default_size(*takenote.DEFAULT_WINDOW_SIZE)
        self.set_icon_list(get_resource_pixbuf("takenote-16x16.png"),
                           get_resource_pixbuf("takenote-32x32.png"),
                           get_resource_pixbuf("takenote-64x64.png"))


        # main window signals
        self.connect("delete-event", lambda w,e: self.on_quit())
        self.connect("window-state-event", self.on_window_state)
        self.connect("size-allocate", self.on_window_size)
        self.app.pref.changed.add(self.on_app_options_changed)
        
        # treeview
        self.treeview = TakeNoteTreeView()
        self.treeview.connect("select-nodes", self.on_tree_select)
        self.treeview.connect("error", lambda w,t,e: self.error(t, e))
        
        # listview
        self.listview = TakeNoteListView()
        self.listview.connect("select-nodes", self.on_list_select)
        self.listview.connect("goto-node", self.on_list_view_node)
        self.listview.connect("goto-parent-node",
                              lambda w: self.on_list_view_parent_node())
        self.listview.connect("error", lambda w,t,e: self.error(t, e))
        self.listview.on_status = self.set_status
        
        
        # editor
        self.editor = TakeNoteEditor()
        self.editor.connect("font-change", self.on_font_change)
        self.editor.connect("modified", self.on_page_editor_modified)
        self.editor.connect("error", lambda w,t,e: self.error(t, e))
        self.editor.connect("child-activated", self.on_child_activated)
        self.editor.view_pages([])


        
        #====================================
        # Dialogs
        
        self.app_options_dialog = dialog_app_options.ApplicationOptionsDialog(self)
        self.find_dialog = dialog_find.TakeNoteFindDialog(self)
        self.drag_test = dialog_drag_drop_test.DragDropTestDialog(self)
        self.image_resize_dialog = \
            dialog_image_resize.ImageResizeDialog(self, self.app.pref)

        # context menus
        self.make_context_menus()
        
        #====================================
        # Layout
        
        # vertical box
        main_vbox = gtk.VBox(False, 0)
        self.add(main_vbox)
        
        # menu bar
        main_vbox.set_border_width(0)
        self.menubar = self.make_menubar()
        main_vbox.pack_start(self.menubar, False, True, 0)
        
        # toolbar
        main_vbox.pack_start(self.make_toolbar(), False, True, 0)          
        
        main_vbox2 = gtk.VBox(False, 0)
        main_vbox2.set_border_width(1)
        main_vbox.pack_start(main_vbox2, True, True, 0)
                
        # create a horizontal paned widget
        self.hpaned = gtk.HPaned()
        main_vbox2.pack_start(self.hpaned, True, True, 0)
        self.hpaned.set_position(takenote.DEFAULT_HSASH_POS)
        
        # status bar
        status_hbox = gtk.HBox(False, 0)
        main_vbox.pack_start(status_hbox, False, True, 0)
        
        # message bar
        self.status_bar = gtk.Statusbar()      
        status_hbox.pack_start(self.status_bar, False, True, 0)
        self.status_bar.set_property("has-resize-grip", False)
        self.status_bar.set_size_request(300, -1)
        
        # stats bar
        self.stats_bar = gtk.Statusbar()
        status_hbox.pack_start(self.stats_bar, True, True, 0)
        

        # layout major widgets
        self.paned2 = gtk.VPaned()
        self.hpaned.add2(self.paned2)
        self.paned2.set_position(takenote.DEFAULT_VSASH_POS)
        
        # treeview and scrollbars
        sw = gtk.ScrolledWindow()
        sw.set_policy(gtk.POLICY_AUTOMATIC, gtk.POLICY_AUTOMATIC)
        sw.set_shadow_type(gtk.SHADOW_IN)
        sw.add(self.treeview)
        self.hpaned.add1(sw)
        
        # listview with scrollbars
        self.listview_sw = gtk.ScrolledWindow()
        self.listview_sw.set_policy(gtk.POLICY_AUTOMATIC, gtk.POLICY_AUTOMATIC)
        self.listview_sw.set_shadow_type(gtk.SHADOW_IN)
        self.listview_sw.add(self.listview)
        self.paned2.add1(self.listview_sw)
        
        # layout editor
        self.paned2.add2(self.editor)

        # load preferences
        self.get_app_preferences()
        self.set_view_mode(self.app.pref.view_mode)

        
        # system tray icon
        if self.app.pref.use_systray and gtk.gtk_version > (2, 10):
            self.tray_icon = gtk.StatusIcon()
            self.tray_icon.set_from_pixbuf(get_resource_pixbuf("takenote-32x32.png"))
            self.tray_icon.set_tooltip("TakeNote")
            self.tray_icon.connect("activate", self.on_tray_icon_activate)
        else:
            self.tray_icon = None
        
        #self.show_all()
        self.treeview.grab_focus()


    #=========================================================
    # main window gui callbacks

    def on_window_state(self, window, event):
        """Callback for window state"""

        # keep track of maximized and minimized state
        self.maximized = event.new_window_state & \
                         gtk.gdk.WINDOW_STATE_MAXIMIZED
        self.iconified = event.new_window_state & \
                         gtk.gdk.WINDOW_STATE_ICONIFIED


    def on_window_size(self, window, event):
        """Callback for resize events"""

        # record window size if it is not maximized or minimized
        if not self.maximized and not self.iconified:
            self.app.pref.window_size = self.get_size()


    def on_app_options_changed(self):
        self.get_app_preferences()


    def on_tray_icon_activate(self, icon):
        """Try icon has been clicked in system tray"""
        self.restore_window()
        
    
    #=============================================================
    # Treeview, listview, editor callbacks
    
    
    def on_tree_select(self, treeview, nodes):
        """Callback for treeview selection change"""

        # do nothing if selection is unchanged
        if self.sel_nodes == nodes:
            return

        self.sel_nodes = nodes
        self.listview.view_nodes(nodes)

        if len(self.queue_list_select) > 0:
            self.listview.select_nodes(self.queue_list_select)
            self.queue_list_select = []
                
        # view pages
        pages = [node for node in nodes 
                 if node.is_page()]
        
        if len(pages) > 0:
            self.listview.select_nodes(pages)
        else:
            self.listview.select_nodes([])

    
    def on_list_select(self, listview, pages):
        """Callback for listview selection change"""

        # TODO: will need to generalize to multiple pages
        
        try:
            if len(pages) > 0:
                self.current_page = pages[0]
            else:
                self.current_page = None
            self.editor.view_pages(pages)
            
        except RichTextError, e:
            self.error("Could not load page '%s'" % pages[0].get_title(),
                       e, sys.exc_traceback)

    def on_list_view_node(self, listview, node):
        """Focus listview on a node"""
        if node is None:
            nodes = self.listview.get_selected_nodes()
            if len(nodes) == 0:
                return
            node = nodes[0]
        
        self.treeview.select_nodes([node])


    def on_list_view_parent_node(self, node=None):
        """Focus listview on a node's parent"""

        # get node
        if node is None:
            if len(self.sel_nodes) == 0:
                return
            if len(self.sel_nodes) > 1 or \
               not self.listview.is_view_tree():
                nodes = self.listview.get_selected_nodes()
                if len(nodes) == 0:
                    return
                node = nodes[0]
            else:
                node = self.sel_nodes[0]

        # get parent
        parent = node.get_parent()
        if parent is None:
            return

        # queue list select
        nodes = self.listview.get_selected_nodes()
        if len(nodes) > 0:
            self.queue_list_select = nodes
        else:
            self.queue_list_select = [node]

        # select parent
        self.treeview.select_nodes([parent])

        
    def on_page_editor_modified(self, editor, page, modified):
        if modified:
            self.set_notebook_modified(modified)


    def on_child_activated(self, editor, textview, child):
        if isinstance(child, richtext.RichTextImage):
            self.view_image(child.get_filename())
    
    
    
    #==============================================
    # Application preferences     
    
    def get_app_preferences(self):
        """Load preferences"""
        self.resize(*self.app.pref.window_size)
        self.paned2.set_position(self.app.pref.vsash_pos)
        self.hpaned.set_position(self.app.pref.hsash_pos)
        
        self.enable_spell_check(self.app.pref.spell_check)

        self.listview.set_date_formats(self.app.pref.timestamp_formats)
        try:
            # if this version of GTK doesn't have tree-lines, ignore it
            self.treeview.set_property("enable-tree-lines",
                                       self.app.pref.treeview_lines)
        except:
            pass
        self.listview.set_rules_hint(self.app.pref.listview_rules)

        if self.app.pref.window_maximized:
            self.maximize()
    

    def set_app_preferences(self):
        """Save preferences"""
        
        self.app.pref.vsash_pos = self.paned2.get_position()
        self.app.pref.hsash_pos = self.hpaned.get_position()
        self.app.pref.window_maximized = self.maximized

        #if textview is not None:
        #    self.app.pref.spell_check = textview.is_spell_check_enabled()

        self.app.pref.write()
           
    #=============================================
    # Notebook open/save/close UI

    def on_new_notebook(self):
        """Launches New NoteBook dialog"""
        
        dialog = gtk.FileChooserDialog("New Notebook", self, 
            action=gtk.FILE_CHOOSER_ACTION_SAVE, #CREATE_FOLDER,
            buttons=("Cancel", gtk.RESPONSE_CANCEL,
                     "New", gtk.RESPONSE_OK))
        dialog.set_current_folder(self.app.pref.new_notebook_path)
        
        response = dialog.run()

        self.app.pref.new_notebook_path = dialog.get_current_folder()
        filename = dialog.get_filename()
        dialog.destroy()
        
        if response == gtk.RESPONSE_OK:            
            self.new_notebook(filename)
            
        elif response == gtk.RESPONSE_CANCEL:
            pass
    
    
    def on_open_notebook(self):
        """Launches Open NoteBook dialog"""
        
        dialog = gtk.FileChooserDialog("Open Notebook", self, 
            action=gtk.FILE_CHOOSER_ACTION_OPEN,
            buttons=("Cancel", gtk.RESPONSE_CANCEL,
                     "Open", gtk.RESPONSE_OK))
        dialog.set_current_folder(self.app.pref.new_notebook_path)        

        
        file_filter = gtk.FileFilter()
        file_filter.add_pattern("*.nbk")
        file_filter.set_name("Notebook (*.nbk)")
        dialog.add_filter(file_filter)
        
        file_filter = gtk.FileFilter()
        file_filter.add_pattern("*")
        file_filter.set_name("All files (*.*)")
        dialog.add_filter(file_filter)
        
        response = dialog.run()

        self.app.pref.new_notebook_path = os.path.dirname(dialog.get_current_folder())
        filename = dialog.get_filename()
        dialog.destroy()

        if response == gtk.RESPONSE_OK:
            self.open_notebook(filename)
            
        elif response == gtk.RESPONSE_CANCEL:
            pass

    
    def on_quit(self):
        """Close the window and quit"""        
        self.close_notebook()
        self.set_app_preferences()
        gtk.accel_map_save(get_accel_file())
        gtk.main_quit()
        return False
    

    
    #===============================================
    # Notebook actions    

    def save_notebook(self, silent=False):
        """Saves the current notebook"""

        if self.notebook is None:
            return
        
        try:
            # TODO: should this be outside exception
            self.editor.save()
            self.notebook.save()

            self.set_status("Notebook saved")
            
            self.set_notebook_modified(False)
            
        except Exception, e:
            if not silent:
                self.error("Could not save notebook", e, sys.exc_traceback)
                self.set_status("Error saving notebook")
                return

            self.set_notebook_modified(False)

        
            
    
    def reload_notebook(self):
        """Reload the current NoteBook"""
        
        if self.notebook is None:
            self.error("Reloading only works when a notebook is open")
            return
        
        filename = self.notebook.get_path()
        self.close_notebook(False)
        self.open_notebook(filename)
        
        self.set_status("Notebook reloaded")
        
        
    
    def new_notebook(self, filename):
        """Creates and opens a new NoteBook"""
        
        if self.notebook is not None:
            self.close_notebook()
        
        try:
            notebook = notebooklib.NoteBook(filename)
            notebook.create()
            self.set_status("Created '%s'" % notebook.get_title())
        except NoteBookError, e:
            self.error("Could not create new notebook", e, sys.exc_traceback)
            self.set_status("")
            return None
        
        notebook = self.open_notebook(filename, new=True)
        self.treeview.expand_node(notebook.get_root_node())
        
        return notebook
        
        
    
    def open_notebook(self, filename, new=False):
        """Opens a new notebook"""
        
        if self.notebook is not None:
            self.close_notebook()
        
        notebook = notebooklib.NoteBook()
        notebook.node_changed.add(self.on_notebook_node_changed)
        
        try:
            notebook.load(filename)
        except NoteBookVersionError, e:
            self.error("This version of TakeNote cannot read this notebook.\n"
                       "The notebook has version %d.  TakeNote can only read %d"
                       % (e.notebook_version, e.readable_version),
                       e, sys.exc_traceback)
            return None
        except NoteBookError, e:            
            self.error("Could not load notebook '%s'" % filename,
                       e, sys.exc_traceback)
            return None

        self.set_notebook(notebook)
        
        self.treeview.grab_focus()
        
        if not new:
            self.set_status("Loaded '%s'" % self.notebook.get_title())
        
        self.set_notebook_modified(False)

        # setup auto-saving
        self.begin_auto_save()
        
        return self.notebook
        
        
    def close_notebook(self, save=True):
        """Close the NoteBook"""
        
        if self.notebook is not None:
            if save:
                try:
                    self.editor.save()
                    self.notebook.save()
                except Exception, e:
                    # TODO: should ask question, like try again?
                    self.error("Could not save notebook",
                               e, sys.exc_traceback)

            self.notebook.node_changed.remove(self.on_notebook_node_changed)
            self.set_notebook(None)
            self.set_status("Notebook closed")



    def begin_auto_save(self):
        """Begin autosave callbacks"""

        if self.app.pref.autosave:
            gobject.timeout_add(self.app.pref.autosave_time, self.auto_save)
        

    def auto_save(self):
        """Callback for autosaving"""

        # NOTE: return True to activate next timeout callback
        
        if self.notebook is not None:
            self.save_notebook(True)
            return self.app.pref.autosave
        else:
            return False
    

    def set_notebook(self, notebook):
        """Set the NoteBook for the window"""
        
        self.notebook = notebook
        self.editor.set_notebook(notebook)
        self.listview.set_notebook(notebook)
        self.treeview.set_notebook(notebook)


    
    #===========================================================
    # page and folder actions

    def get_selected_nodes(self, widget="focus"):
        """
        Returns (nodes, widget) where 'nodes' are a list of selected nodes
        in widget 'widget'

        Wiget can be
           listview -- nodes selected in listview
           treeview -- nodes selected in treeview
           focus    -- nodes selected in widget with focus
        """
        
        if widget == "focus":
            if self.listview.is_focus():
                widget = "listview"
            elif self.treeview.is_focus():
                widget = "treeview"
            elif self.editor.is_focus():
                widget = "listview"
            else:
                return ([], "")

        if widget == "treeview":
            nodes = self.treeview.get_selected_nodes()
        elif widget == "listview":
            nodes = self.listview.get_selected_nodes()
        else:
            raise Exception("unknown widget '%s'" % widget)

        return (nodes, widget)
        
    
    def on_new_dir(self, widget="focus"):
        """Add new folder near selected nodes"""

        if self.notebook is None:
            return

        nodes, widget = self.get_selected_nodes(widget)
        
        if len(nodes) == 1:
            parent = nodes[0]
        else:
            parent = self.notebook.get_root_node()
        
        if parent.is_page():
            parent = parent.get_parent()
        node = parent.new_dir()

        if widget == "treeview":
            self.treeview.expand_node(parent)
            self.treeview.edit_node(node)
            
        elif widget == "listview":
            self.listview.expand_node(parent)
            self.listview.edit_node(node)
            
        elif widget == "":
            pass
        
        else:
            raise Exception("unknown widget '%s'" % widget)            
    
            
    
    def on_new_page(self, widget="focus"):
        """Add new page near selected nodes"""

        if self.notebook is None:
            return

        nodes, widget = self.get_selected_nodes(widget)
        
        if len(nodes) == 1:
            parent = nodes[0]
        else:
            parent = self.notebook.get_root_node()

        if parent.is_page():
            parent = parent.get_parent()
        node = parent.new_page()
        
        if widget == "treeview":
            self.treeview.expand_node(parent)
            self.treeview.edit_node(node)
        elif widget == "listview":
            self.listview.expand_node(parent)
            self.listview.edit_node(node)
        elif widget == "":
            pass
        else:
            raise Exception("unknown widget '%s'" % widget)       
    

    def on_empty_trash(self):
        """Empty Trash folder in NoteBook"""
        
        if self.notebook is None:
            return

        try:
            self.notebook.empty_trash()
        except NoteBookError, e:
            self.error("Could not empty trash.", e, sys.exc_traceback)



    def on_search_nodes(self):
        """Search nodes"""
        if not self.notebook:
            return

        words = [x.lower() for x in
                 self.search_box.get_text().strip().split()]
        nodes = takenote.search.search_manual(self.notebook, words)
        self.listview.view_nodes(nodes, nested=False)


    def focus_on_search_box(self):
        self.search_box.grab_focus()
    
    #=====================================================
    # Notebook callbacks
    
    def on_notebook_node_changed(self, nodes, recurse):
        self.set_notebook_modified(True)
        
    
    def set_notebook_modified(self, modified):
        if self.notebook is None:
            self.set_title(takenote.PROGRAM_NAME)
        else:
            if modified:
                self.set_title("* %s" % self.notebook.get_title())
                self.set_status("Notebook modified")
            else:
                self.set_title("%s" % self.notebook.get_title())
    
    
    #=================================================
    # view config
        
    def set_view_mode(self, mode):
        """Sets the view mode of the window
        
        modes:
            "vertical"
            "horizontal"
        """

        if self.ignore_view_mode:
            return

        self.ignore_view_mode = True
        
        self.paned2.remove(self.listview_sw)
        self.paned2.remove(self.editor)
        self.hpaned.remove(self.paned2)
        
        if mode == "vertical":
            # create a vertical paned widget
            self.paned2 = gtk.VPaned()
            self.view_mode_h_toggle.set_active(False)
            self.view_mode_v_toggle.set_active(True)
        else:
            self.paned2 = gtk.HPaned()
            self.view_mode_h_toggle.set_active(True)
            self.view_mode_v_toggle.set_active(False)            
        self.paned2.set_position(self.app.pref.vsash_pos)
        self.paned2.show()
        
        self.hpaned.add2(self.paned2)
        self.hpaned.show()
        
        self.paned2.add1(self.listview_sw)
        self.paned2.add2(self.editor)
        
        self.app.pref.view_mode = mode
        self.app.pref.write()

        self.ignore_view_mode = False
    
    #=============================================================
    # Update UI (menubar) from font under cursor
    
    def on_font_change(self, editor, font):
        
        # block toolbar handlers
        for ui in self.font_ui_signals:
            ui.widget.handler_block(ui.signal)

        # update font mods
        self.bold_button.set_active(font.mods["bold"])
        self.italic_button.set_active(font.mods["italic"])
        self.underline_button.set_active(font.mods["underline"])
        self.fixed_width_button.set_active(font.family == "Monospace")
        self.no_wrap_button.set_active(font.mods["nowrap"])
        
        # update text justification
        self.left_button.set_active(font.justify == "left")
        self.center_button.set_active(font.justify == "center")
        self.right_button.set_active(font.justify == "right")
        self.fill_button.set_active(font.justify == "fill")

        # update bullet list
        self.bullet_button.set_active(font.par_type == "bullet")
        
        # update family/size buttons        
        self.font_family_combo.set_family(font.family)
        self.font_size_button.set_value(font.size)
        
        # unblock toolbar handlers
        for ui in self.font_ui_signals:
            ui.widget.handler_unblock(ui.signal)


    #==================================================
    # changing font handlers

    def on_mod(self, mod, mod_button, mod_id):
        self.editor.get_textview().toggle_font_mod(mod)
        font = self.editor.get_textview().get_font()
        
        mod_button.handler_block(mod_id)
        mod_button.set_active(font.mods[mod])
        mod_button.handler_unblock(mod_id)

    def on_bold(self):
        self.on_mod("bold", self.bold_button, self.bold_id)
    
    def on_italic(self):
        self.on_mod("italic", self.italic_button, self.italic_id)
    
    def on_underline(self):
        self.on_mod("underline", self.underline_button, self.underline_id)
    
    def on_fixed_width(self, toolbar):
        self.editor.get_textview().toggle_font_family("Monospace")    
        
        if not toolbar:
            font = self.editor.get_textview().get_font()
        
            self.fixed_width_button.handler_block(self.fixed_width_id)        
            self.fixed_width_button.set_active(font.family == "Monospace")
            self.fixed_width_button.handler_unblock(self.fixed_width_id)

    def on_no_wrap(self):
        self.on_mod("nowrap", self.no_wrap_button, self.no_wrap_id)        

    def on_justify(self, justify):
        self.editor.get_textview().set_justify(justify)
        font = self.editor.get_textview().get_font()
        self.on_font_change(self.editor, font)
        
    def on_bullet_list(self):
        """Toggle bullet list"""
        self.editor.get_textview().toggle_bullet()
        font = self.editor.get_textview().get_font()
        self.on_font_change(self.editor, font)
        
    def on_indent(self):
        """Indent current paragraph"""
        self.editor.get_textview().indent()

    def on_unindent(self):
        """Unindent current paragraph"""
        self.editor.get_textview().unindent()


    def on_choose_font(self):
        """Callback for opening Choose Font Dialog"""
        
        font = self.editor.get_textview().get_font()

        dialog = gtk.FontSelectionDialog("Choose Font")
        dialog.set_font_name("%s %d" % (font.family, font.size))
        response = dialog.run()

        if response == gtk.RESPONSE_OK:
            self.editor.get_textview().set_font(dialog.get_font_name())
            self.editor.get_textview().grab_focus()

        dialog.destroy()
        
    
    def on_family_set(self):
        self.editor.get_textview().set_font_family(self.font_family_combo.get_family())
        self.editor.get_textview().grab_focus()
        

    def on_font_size_change(self, size):
        self.editor.get_textview().set_font_size(size)
        self.editor.get_textview().grab_focus()
    
    def on_font_size_inc(self):
        font = self.editor.get_textview().get_font()
        font.size += 2        
        self.editor.get_textview().set_font_size(font.size)
        self.on_font_change(self.editor, font)
    
    
    def on_font_size_dec(self):
        font = self.editor.get_textview().get_font()
        if font.size > 4:
            font.size -= 2
        self.editor.get_textview().set_font_size(font.size)
        self.on_font_change(self.editor, font)


    def on_color_set(self, kind, color=0):
        """Set text/background color"""
        
        if color == 0:
            if kind == "fg":
                color = self.fg_color_button.color
            elif kind == "bg":
                color = self.bg_color_button.color
            else:
                color = None

        if color is not None:
            colorstr = color_tuple_to_string(color)
        else:
            colorstr = None

        if kind == "fg":
            self.editor.get_textview().set_font_fg_color(colorstr)
        elif kind == "bg":
            self.editor.get_textview().set_font_bg_color(colorstr)
        else:
            raise Exception("unknown color type '%s'" % str(kind))
        
        

    #=================================================
    # Window manipulation

    def minimize_window(self):
        """Minimize the window (block until window is minimized"""
        
        # TODO: add timer in case minimize fails
        def on_window_state(window, event):            
            if event.new_window_state & gtk.gdk.WINDOW_STATE_ICONIFIED:
                gtk.main_quit()
        sig = self.connect("window-state-event", on_window_state)
        self.iconify()
        gtk.main()
        self.disconnect(sig)

    def restore_window(self):
        """Restore the window from minimization"""
        self.deiconify()
        self.present()
        
    #==================================================
    # Image/screenshot actions

    def on_screenshot(self):
        """Take and insert a screen shot image"""

        # do nothing if no page is selected
        if self.current_page is None:
            return

        # Minimize window
        self.minimize_window()
        
        # TODO: generalize
        try:
            if takenote.get_platform() == "windows":
                # use win32api to take screenshot
                # create temp file
                f, imgfile = tempfile.mkstemp(".bmp", "takenote")
                os.close(f)
                screenshot_win.take_screenshot(imgfile)
            else:
                # use external app for screen shot
                screenshot = self.app.pref.get_external_app("screen_shot")
                if screenshot is None:
                    self.error("You must specify a Screen Shot program in Application Options")
                    return

                # create temp file
                f, imgfile = tempfile.mkstemp(".png", "takenote")
                os.close(f)

                try:
                    proc = subprocess.Popen([screenshot.prog, imgfile])
                    if proc.wait() != 0:
                        raise OSError("Exited with error")
                except OSError, e:
                    raise e
            
        except Exception, e:        
            # catch exceptions for screenshot program
            self.restore_window()
            self.error("The screenshot program encountered an error", e,
                       sys.exc_traceback)
            
        else:
            if not os.path.exists(imgfile):
                # catch error if image is not created
                self.restore_window()
                self.error("The screenshot program did not create the necessary image file '%s'" % imgfile)
            else:
                # insert image
                try:
                    self.insert_image(imgfile, "screenshot.png")
                except Exception, e:
                    # TODO: make exception more specific
                    self.restore_window()
                    self.error("Error importing screenshot '%s'" % imgfile,
                               e, sys.exc_traceback)
            
        # remove temp file
        try:
            os.remove(imgfile)
        except OSError, e:
            self.restore_window()
            self.error("%s was unable to remove temp file for screenshot" %
                       takenote.PROGRAM_NAME, e, sys.exc_traceback)

        self.restore_window()


    def on_insert_hr(self):
        """Insert horizontal rule into editor"""
        if self.current_page is None:
            return
        
        self.editor.get_textview().insert_hr()
        
    def on_insert_image(self):
        """Displays the Insert Image Dialog"""
        if self.current_page is None:
            return
                  
        dialog = gtk.FileChooserDialog("Insert Image From File", self, 
            action=gtk.FILE_CHOOSER_ACTION_OPEN,
            buttons=("Cancel", gtk.RESPONSE_CANCEL,
                     "Insert", gtk.RESPONSE_OK))
        dialog.set_current_folder(self.app.pref.insert_image_path)
        

        # run dialog
        response = dialog.run()


        self.app.pref.insert_image_path = dialog.get_current_folder()
        
        if response == gtk.RESPONSE_OK:
            filename = dialog.get_filename()
            dialog.destroy()
                        
            imgname, ext = os.path.splitext(os.path.basename(filename))
            if ext == ".jpg":
                imgname = imgname + ".jpg"
            else:
                imgname = imgname + ".png"
            
            try:
                self.insert_image(filename, imgname)
            except Exception, e:
                # TODO: make exception more specific
                self.error("Could not insert image '%s'" % filename, e,
                           sys.exc_traceback)
            
        elif response == gtk.RESPONSE_CANCEL:
            dialog.destroy()
        
    
    
    def insert_image(self, filename, savename="image.png"):
        """Inserts an image into the text editor"""

        if self.current_page is None:
            return
        
        pixbuf = gdk.pixbuf_new_from_file(filename)
        img = RichTextImage()
        img.set_from_pixbuf(pixbuf)
        self.editor.get_textview().insert_image(img, savename)


    #=================================================
    # Image context menu

    def on_view_image(self, menuitem):
        """View image in Image Viewer"""

        if self.current_page is None:
            return
        
        # get image filename
        image_filename = menuitem.get_parent().get_child().get_filename()
        self.view_image(image_filename)
        

    def view_image(self, image_filename):
        image_path = os.path.join(self.current_page.get_path(), image_filename)
        viewer = self.app.pref.get_external_app("image_viewer")
        
        if viewer is not None:
            try:
                proc = subprocess.Popen([viewer.prog, image_path])
            except OSError, e:
                self.error("Could not open Image Viewer", e, sys.exc_traceback)
        else:
            self.error("You specify an Image Viewer in Application Options""")


    def on_edit_image(self, menuitem):
        """Edit image in Image Editor"""

        if self.current_page is None:
            return
        
        # get image filename
        image_filename = menuitem.get_parent().get_child().get_filename()

        image_path = os.path.join(self.current_page.get_path(), image_filename)
        editor = self.app.pref.get_external_app("image_editor")
    
        if editor is not None:
            try:
                proc = subprocess.Popen([editor.prog, image_path])
            except OSError, e:
                self.error("Could not open Image Editor", e, sys.exc_traceback)
        else:
            self.error("You specify an Image Editor in Application Options""")


    def on_resize_image(self, menuitem):
        """Resize image"""
        
        if self.current_page is None:
            return
        
        image = menuitem.get_parent().get_child()
        self.image_resize_dialog.on_resize(image)
        


    def on_save_image_as(self, menuitem):
        """Save image as a new file"""
        
        if self.current_page is None:
            return
        
        # get image filename
        image = menuitem.get_parent().get_child()
        image_filename = menuitem.get_parent().get_child().get_filename()
        image_path = os.path.join(self.current_page.get_path(), image_filename)

        dialog = gtk.FileChooserDialog("Save Image As...", self, 
            action=gtk.FILE_CHOOSER_ACTION_SAVE,
            buttons=("Cancel", gtk.RESPONSE_CANCEL,
                     "Save", gtk.RESPONSE_OK))
        dialog.set_default_response(gtk.RESPONSE_OK)
        dialog.set_current_folder(self.app.pref.save_image_path)
        
        response = dialog.run()

        self.app.pref.save_image_path = dialog.get_current_folder()

        if response == gtk.RESPONSE_OK:
            if dialog.get_filename() == "":
                self.error("Must specify a filename for the image.")
            else:
                try:                
                    image.write(dialog.get_filename())
                except Exception, e:
                    self.error("Could not save image '%s'" %
                               dialog.get_filename(), e, sys.exc_traceback)

        dialog.destroy()
                            
        
        
                
    #=============================================
    # Goto menu options
    
    def on_goto_treeview(self):
        """Switch focus to TreeView"""
        self.treeview.grab_focus()
        
    def on_goto_listview(self):
        """Switch focus to ListView"""
        self.listview.grab_focus()
        
    def on_goto_editor(self):
        """Switch focus to Editor"""
        self.editor.get_textview().grab_focus()
    
    
    
    #=====================================================
    # Cut/copy/paste

    # NOTE: for now all copy/cut/paste is sent to textview
    
    def on_cut(self):
        """Cut callback"""
        self.editor.get_textview().emit("cut-clipboard")
    
    def on_copy(self):
        """Copy callback"""
        self.editor.get_textview().emit("copy-clipboard")
    
    def on_paste(self):
        """Paste callback"""
        self.editor.get_textview().emit("paste-clipboard")
    
    
    #=====================================================
    # External app viewers

    def on_view_node_external_app(self, app, node=None, widget="focus",
                                  page_only=False):
        """View a node with an external app"""
        
        if node is None:
            nodes, widget = self.get_selected_nodes(widget)
            if len(nodes) == 0:
                self.error("No notes are selected.")                
                return            
            node = nodes[0]

            if page_only and not node.is_page():
                self.error("Only pages can be viewed with %s." %
                           self.app.external_apps[app].title)
                return

        try:
            if page_only:
                filename = os.path.realpath(node.get_data_file())
            else:
                filename = os.path.realpath(node.get_path())
            self.app.run_external_app(app, filename)
        except TakeNoteError, e:
            self.error(e.msg, e, sys.exc_traceback)


    def view_error_log(self):        
        """View error in text editor"""

        # windows locks open files
        # therefore we should copy error log before viewing it
        try:
            filename = os.path.realpath(takenote.get_user_error_log())
            filename2 = filename + ".bak"
            shutil.copy(filename, filename2)        

            # use text editor to view error log
            self.app.run_external_app("text_editor", filename2)
        except Exception, e:
            self.error("Could not open error log", e, sys.exc_traceback)
                                       
    
    def on_spell_check_toggle(self, num, widget):
        """Toggle spell checker"""

        textview = self.editor.get_textview()
        if textview is not None:
            self.enable_spell_check(widget.get_active())


    def enable_spell_check(self, enabled):
        """Spell check"""

        textview = self.editor.get_textview()
        if textview is not None:
            textview.enable_spell_check(enabled)
            
            # see if spell check became enabled
            enabled = textview.is_spell_check_enabled()
            self.app.pref.spell_check = enabled
            self.spell_check_toggle.set_active(enabled)
    
    #==================================================
    # Help/about dialog
    
    def on_about(self):
        """Display about dialog"""
        
        about = gtk.AboutDialog()
        about.set_name(takenote.PROGRAM_NAME)
        about.set_version(takenote.PROGRAM_VERSION_TEXT)
        about.set_copyright("Copyright Matt Rasmussen 2008")
        about.set_logo(get_resource_pixbuf("takenote-icon.png"))
        about.set_website(takenote.WEBSITE)
        about.set_transient_for(self)
        about.set_position(gtk.WIN_POS_CENTER_ON_PARENT)
        about.connect("response", lambda d,r: about.destroy())
        about.show()

        # gtk.about_dialog_set_url_hook(func, data)
        # def func(dialog, link, user_data)
        

    #===========================================
    # Messages, warnings, errors UI/dialogs
    
    def set_status(self, text, bar="status"):
        if bar == "status":
            self.status_bar.pop(0)
            self.status_bar.push(0, text)
        elif bar == "stats":
            self.stats_bar.pop(0)
            self.stats_bar.push(0, text)
        else:
            raise Exception("unknown bar '%s'" % bar)
            
    
    def error(self, text, error=None, tracebk=None):
        """Display an error message"""
        
        dialog = gtk.MessageDialog(self.get_toplevel(), 
            flags= gtk.DIALOG_MODAL | gtk.DIALOG_DESTROY_WITH_PARENT,
            type=gtk.MESSAGE_ERROR, 
            buttons=gtk.BUTTONS_OK, 
            message_format=text)
        dialog.connect("response", lambda d,r: d.destroy())
        dialog.set_title("Error")
        dialog.show()

        # add message to error log
        if error is not None:
            sys.stderr.write("\n")
            traceback.print_exception(type(error), error, tracebk)
    
    
    #================================================
    # Menus
    
    def make_menubar(self):
        """Initialize the menu bar"""
        
        self.menu_items = (
            ("/_File",               
                None, None, 0, "<Branch>"),
            ("/File/_New Notebook",
                "", lambda w,e: self.on_new_notebook(), 0, 
                "<StockItem>", gtk.STOCK_NEW),
            ("/File/New _Page",      
                "<control>N", lambda w,e: self.on_new_page(), 0, 
                "<ImageItem>", 
                get_resource_pixbuf("note-new.png")),
            ("/File/New _Folder", 
                "<control><shift>N", lambda w,e: self.on_new_dir(), 0, 
                "<ImageItem>", 
                get_resource_pixbuf("folder-new.png")),

            #("/File/sep1", 
            #    None, None, 0, "<Separator>" ),                
            #("/File/New _Tab",
            #    "<control>T", lambda w,e: self.editor.new_tab(), 0, None),
            #("/File/C_lose Tab", 
            #    "<control>W", lambda w,e: self.editor.close_tab(), 0, None),
                
            ("/File/sep2", 
                None, None, 0, "<Separator>" ),
            ("/File/_Open Notebook",          
                "<control>O", lambda w,e: self.on_open_notebook(), 0,
                "<StockItem>", gtk.STOCK_OPEN),             
                #"<ImageItem>", 
                #get_resource_pixbuf("open.png")),
            ("/File/_Reload Notebook",          
                None, lambda w,e: self.reload_notebook(), 0, 
                "<StockItem>", gtk.STOCK_REVERT_TO_SAVED),
            ("/File/_Save Notebook",     
                "<control>S", lambda w,e: self.save_notebook(), 0,
                "<StockItem>", gtk.STOCK_SAVE),
                #"<ImageItem>", 
                #get_resource_pixbuf("save.png")),
            ("/File/_Close Notebook", 
                None, lambda w, e: self.close_notebook(), 0, 
                "<StockItem>", gtk.STOCK_CLOSE),

            #("/File/sep3", 
            #    None, None, 0, "<Separator>" ),

            #("/File/_Backup Notebook",
            # None, lambda w, e: self.on_archive_notebook(), 0,
            #    None),
            #("/File/R_estore Notebook",
            # None, lambda w, e: self.on_restore_notebook(), 0,
            #    None),
            
            ("/File/sep4", 
                None, None, 0, "<Separator>" ),
            ("/File/Quit", 
                "<control>Q", lambda w,e: self.on_quit(), 0, None),

            ("/_Edit", 
                None, None, 0, "<Branch>"),
            ("/Edit/_Undo", 
                "<control>Z", lambda w,e: self.editor.get_textview().undo(), 0, 
                "<StockItem>", gtk.STOCK_UNDO),
            ("/Edit/_Redo", 
                "<control><shift>Z", lambda w,e: self.editor.get_textview().redo(), 0, 
                "<StockItem>", gtk.STOCK_REDO),
            ("/Edit/sep1", 
                None, None, 0, "<Separator>"),
            ("/Edit/Cu_t", 
                "<control>X", lambda w,e: self.on_cut(), 0, 
                "<StockItem>", gtk.STOCK_CUT), 
            ("/Edit/_Copy",     
                "<control>C", lambda w,e: self.on_copy(), 0, 
                "<StockItem>", gtk.STOCK_COPY), 
            ("/Edit/_Paste",     
                "<control>V", lambda w,e: self.on_paste(), 0, 
                "<StockItem>", gtk.STOCK_PASTE), 
            
            
            #("/Edit/sep3", 
            #    None, None, 0, "<Separator>"),
            #("/Edit/_Delete Folder",
            #    None, lambda w,e: self.on_delete_dir(), 0, 
            #    "<ImageItem>", folder_delete.get_pixbuf()),
            #("/Edit/Delete _Page",     
            #    None, lambda w,e: self.on_delete_page(), 0,
            #    "<ImageItem>", page_delete.get_pixbuf()),
            ("/Edit/sep4", 
                None, None, 0, "<Separator>"),
            ("/Edit/Insert _Horizontal Rule",
                "<control>H", lambda w,e: self.on_insert_hr(), 0, None),
            ("/Edit/Insert _Image",
                None, lambda w,e: self.on_insert_image(), 0, None),
            ("/Edit/Insert _Screenshot",
                "<control>Insert", lambda w,e: self.on_screenshot(), 0, None),

            ("/Edit/sep5", 
                None, None, 0, "<Separator>"),
            ("/Edit/Empty _Trash",
             None, lambda w,e: self.on_empty_trash(), 0,
             "<StockItem>", gtk.STOCK_DELETE),
            
            
            ("/_Search", None, None, 0, "<Branch>"),
            ("/Search/_Search All Notes",
             "<control>K", lambda w,e: self.focus_on_search_box(), 0,
             "<StockItem>", gtk.STOCK_FIND),
            ("/Search/_Find In Page",     
                "<control>F", lambda w,e: self.find_dialog.on_find(False), 0, 
                "<StockItem>", gtk.STOCK_FIND), 
            ("/Search/Find _Next In Page",     
                "<control>G", lambda w,e: self.find_dialog.on_find(False, forward=True), 0, 
                "<StockItem>", gtk.STOCK_FIND), 
            ("/Search/Find Pre_vious In Page",     
                "<control><shift>G", lambda w,e: self.find_dialog.on_find(False, forward=False), 0, 
                "<StockItem>", gtk.STOCK_FIND),                 
            ("/Search/_Replace In Page",     
                "<control><shift>R", lambda w,e: self.find_dialog.on_find(True), 0, 
                "<StockItem>", gtk.STOCK_FIND), 
                
            
            ("/Fo_rmat", 
             None, None, 0, "<Branch>"),

            ("/Format/_Bold", 
             "<control>B", lambda w,e: self.on_bold(), 0, 
             "<ImageItem>", 
             get_resource_pixbuf("bold.png")),
            ("/Format/_Italic", 
             "<control>I", lambda w,e: self.on_italic(), 0, 
             "<ImageItem>", 
             get_resource_pixbuf("italic.png")),
            ("/Format/_Underline", 
             "<control>U", lambda w,e: self.on_underline(), 0, 
             "<ImageItem>", 
             get_resource_pixbuf("underline.png")),
            ("/Format/_Monospace",
             "<control>M", lambda w,e: self.on_fixed_width(False), 0,
             "<ImageItem>",
             get_resource_pixbuf("fixed-width.png")),
            ("/Format/No _Wrapping",
             None, lambda w, e: self.on_no_wrap(), 0,
             "<ImageItem>",
             get_resource_pixbuf("no-wrap.png")),
            
            ("/Format/sep1",
             None, None, 0, "<Separator>" ),            
            
            ("/Format/_Left Align", 
             "<control>L", lambda w,e: self.on_justify("left"), 0, 
             "<ImageItem>", 
             get_resource_pixbuf("alignleft.png")),
            ("/Format/C_enter Align", 
             "<control>E", lambda w,e: self.on_justify("center"), 0, 
             "<ImageItem>", 
             get_resource_pixbuf("aligncenter.png")),
            ("/Format/_Right Align", 
             "<control>R", lambda w,e: self.on_justify("right"), 0, 
             "<ImageItem>", 
             get_resource_pixbuf("alignright.png")),
            ("/Format/_Justify Align", 
             "<control>J", lambda w,e: self.on_justify("fill"), 0, 
             "<ImageItem>", 
             get_resource_pixbuf("alignjustify.png")),
            ("/Format/sep2",
             None, None, 0, "<Separator>" ),

            ("/Format/_Bullet List", 
             "<control>asterisk", lambda w,e: self.on_bullet_list(), 0, 
             "<ImageItem>", 
             get_resource_pixbuf("bullet.png")),
            ("/Format/Indent M_ore", 
             "<control>parenright", lambda w,e: self.on_indent(), 0, 
             "<ImageItem>", 
             get_resource_pixbuf("indent-more.png")),     
            ("/Format/Indent Le_ss", 
             "<control>parenleft", lambda w,e: self.on_unindent(), 0, 
             "<ImageItem>", 
             get_resource_pixbuf("indent-less.png")),
            
            ("/Format/sep4", 
                None, None, 0, "<Separator>" ),
            ("/Format/Increase Font _Size", 
                "<control>equal", lambda w, e: self.on_font_size_inc(), 0, 
                "<ImageItem>", 
                get_resource_pixbuf("font-inc.png")),
            ("/Format/_Decrease Font Size", 
                "<control>minus", lambda w, e: self.on_font_size_dec(), 0, 
                "<ImageItem>", 
                get_resource_pixbuf("font-dec.png")),

            ("/Format/sep5", 
                None, None, 0, "<Separator>" ),
            ("/Format/_Apply Text Color", 
                "", lambda w, e: self.on_color_set("fg"), 0),
            ("/Format/A_pply Background Color", 
                "", lambda w, e: self.on_color_set("bg"), 0),
            
            
            ("/Format/sep6", 
                None, None, 0, "<Separator>" ),
            ("/Format/Choose _Font", 
                "<control><shift>F", lambda w, e: self.on_choose_font(), 0, 
                "<ImageItem>", 
                get_resource_pixbuf("font.png")),

            
            ("/_View", None, None, 0, "<Branch>"),
            ("/View/View Note in File Explorer",
             None, lambda w,e:
             self.on_view_node_external_app("file_explorer"), 0, 
             "<ImageItem>",
             get_resource_pixbuf("note.png")),
            ("/View/View Note in Text Editor",
             None, lambda w,e:
             self.on_view_node_external_app("text_editor", page_only=True), 0, 
             "<ImageItem>",
             get_resource_pixbuf("note.png")),
            ("/View/View Note in Web Browser",
             None, lambda w,e:
             self.on_view_node_external_app("web_browser", page_only=True), 0, 
             "<ImageItem>",
             get_resource_pixbuf("note.png")),
            
            
            ("/_Go", None, None, 0, "<Branch>"),
            ("/_Go/Go to _Note",
                None, lambda w,e: self.on_list_view_node(None, None), 0,
                "<StockItem>", gtk.STOCK_GO_DOWN),
            ("/_Go/Go to _Parent Note",
                None, lambda w,e: self.on_list_view_parent_node(), 0,
                "<StockItem>", gtk.STOCK_GO_UP),            

            ("/Go/sep1", None, None, 0, "<Separator>"),

            ("/Go/Go to _Tree View",
                "<control>T", lambda w,e: self.on_goto_treeview(), 0, None),
            ("/Go/Go to _List View",
                "<control>Y", lambda w,e: self.on_goto_listview(), 0, None),
            ("/Go/Go to _Editor",
                "<control>D", lambda w,e: self.on_goto_editor(), 0, None),
            
            ("/_Options", None, None, 0, "<Branch>"),
            ("/Options/_Spell check", 
                None, self.on_spell_check_toggle, 0,
                "<ToggleItem>"),
                
            ("/Options/sep1", None, None, 0, "<Separator>"),
            ("/Options/_Horizontal Layout",
                None, lambda w,e: self.set_view_mode("horizontal"), 0, 
                "<ToggleItem>"),
            ("/Options/_Vertical Layout",
                None, lambda w,e: self.set_view_mode("vertical"), 0, 
                "<ToggleItem>"),
                
            ("/Options/sep1", None, None, 0, "<Separator>"),
            ("/Options/_TakeNote Options",
                None, lambda w,e: self.app_options_dialog.on_app_options(), 0, 
                "<StockItem>", gtk.STOCK_PREFERENCES),
            
            ("/_Help",       None, None, 0, "<Branch>" ),
            ("/Help/View Error Log...",
             None, lambda w,e: self.view_error_log(), 0, None),
            ("/Help/Drag and Drop Test...",
                None, lambda w,e: self.drag_test.on_drag_and_drop_test(),
                0, None),
            ("/Help/sep1", None, None, 0, "<Separator>"),
            ("/Help/About", None, lambda w,e: self.on_about(), 0, None ),
            )    
    
        accel_group = gtk.AccelGroup()
        accel_file = get_accel_file()
        if os.path.exists(accel_file):
            gtk.accel_map_load(accel_file)
        else:
            gtk.accel_map_save(accel_file)
            

        # Create item factory
        self.item_factory = gtk.ItemFactory(gtk.MenuBar, "<main>", accel_group)
        self.item_factory.create_items(self.menu_items)
        self.add_accel_group(accel_group)

        # view mode
        self.view_mode_h_toggle = \
            self.item_factory.get_widget("/Options/Horizontal Layout")
        self.view_mode_v_toggle = \
            self.item_factory.get_widget("/Options/Vertical Layout")

        # get spell check toggle
        self.spell_check_toggle = \
            self.item_factory.get_widget("/Options/Spell check")
        self.spell_check_toggle.set_sensitive(
            self.editor.get_textview().can_spell_check())


        self.menubar_file_extensions = \
            self.item_factory.get_widget("/File/Close Notebook")
        
        return self.item_factory.get_widget("<main>")


    
    def make_toolbar(self):
        
        toolbar = gtk.Toolbar()
        toolbar.set_orientation(gtk.ORIENTATION_HORIZONTAL)
        toolbar.set_style(gtk.TOOLBAR_ICONS)

        try:
            # NOTE: if this version of GTK doesn't have this size, then
            # ignore it
            toolbar.set_property("icon-size", gtk.ICON_SIZE_SMALL_TOOLBAR)
        except:
            pass
        
        toolbar.set_border_width(0)
        
        tips = gtk.Tooltips()
        tips.enable()

        # open notebook
        #button = gtk.ToolButton()
        #if self.app.pref.use_stock_icons:
        #    button.set_stock_id(gtk.STOCK_OPEN)
        #else:
        #    button.set_icon_widget(get_resource_image("open.png"))
        #tips.set_tip(button, "Open Notebook")
        #button.connect("clicked", lambda w: self.on_open_notebook())
        #toolbar.insert(button, -1)

        # save notebook
        #button = gtk.ToolButton()
        #if self.app.pref.use_stock_icons:
        #    button.set_stock_id(gtk.STOCK_SAVE)
        #else:
        #    button.set_icon_widget(get_resource_image("save.png"))
        #tips.set_tip(button, "Save Notebook")
        #button.connect("clicked", lambda w: self.save_notebook())
        #toolbar.insert(button, -1)        

        # separator
        #toolbar.insert(gtk.SeparatorToolItem(), -1)        

        # new folder
        button = gtk.ToolButton()
        if self.app.pref.use_stock_icons:
            button.set_stock_id(gtk.STOCK_DIRECTORY)
        else:
            button.set_icon_widget(get_resource_image("folder-new.png"))
        tips.set_tip(button, "New Folder")
        button.connect("clicked", lambda w: self.on_new_dir())
        toolbar.insert(button, -1)

        # new page
        button = gtk.ToolButton()
        if self.app.pref.use_stock_icons:
            button.set_stock_id(gtk.STOCK_NEW)
        else:
            button.set_icon_widget(get_resource_image("note-new.png"))
        tips.set_tip(button, "New Page")
        button.connect("clicked", lambda w: self.on_new_page())
        toolbar.insert(button, -1)

        # separator
        toolbar.insert(gtk.SeparatorToolItem(), -1)        


        # goto note
        button = gtk.ToolButton()
        button.set_stock_id(gtk.STOCK_GO_DOWN)
        tips.set_tip(button, "Go to Note")
        button.connect("clicked", lambda w: self.on_list_view_node(None, None))
        toolbar.insert(button, -1)        
        
        # goto parent node
        button = gtk.ToolButton()
        button.set_stock_id(gtk.STOCK_GO_UP)
        tips.set_tip(button, "Go to Parent Note")
        button.connect("clicked", lambda w: self.on_list_view_parent_node())
        toolbar.insert(button, -1)        


        # separator
        toolbar.insert(gtk.SeparatorToolItem(), -1)        

        
        # bold tool
        self.bold_button = gtk.ToggleToolButton()
        if self.app.pref.use_stock_icons:
            self.bold_button.set_stock_id(gtk.STOCK_BOLD)
        else:
            self.bold_button.set_icon_widget(get_resource_image("bold.png"))
        tips.set_tip(self.bold_button, "Bold")
        self.bold_id = self.bold_button.connect("toggled",
            lambda w: self.editor.get_textview().toggle_font_mod("bold"))
        toolbar.insert(self.bold_button, -1)
        self.font_ui_signals.append(FontUI(self.bold_button, self.bold_id))


        # italic tool
        self.italic_button = gtk.ToggleToolButton()
        if self.app.pref.use_stock_icons:
            self.italic_button.set_stock_id(gtk.STOCK_ITALIC)
        else:
            self.italic_button.set_icon_widget(get_resource_image("italic.png"))
        tips.set_tip(self.italic_button, "Italic")
        self.italic_id = self.italic_button.connect("toggled",
            lambda w: self.editor.get_textview().toggle_font_mod("italic"))
        toolbar.insert(self.italic_button, -1)
        self.font_ui_signals.append(FontUI(self.italic_button, self.italic_id))

        # underline tool
        self.underline_button = gtk.ToggleToolButton()
        if self.app.pref.use_stock_icons:
            self.underline_button.set_stock_id(gtk.STOCK_UNDERLINE)
        else:
            self.underline_button.set_icon_widget(
                get_resource_image("underline.png"))
        tips.set_tip(self.underline_button, "Underline")            
        self.underline_id = self.underline_button.connect("toggled",
            lambda w: self.editor.get_textview().toggle_font_mod("underline"))
        toolbar.insert(self.underline_button, -1)
        self.font_ui_signals.append(FontUI(self.underline_button,
                                           self.underline_id))
        
        # fixed-width tool
        self.fixed_width_button = gtk.ToggleToolButton()
        self.fixed_width_button.set_icon_widget(
            get_resource_image("fixed-width.png"))
        tips.set_tip(self.fixed_width_button, "Monospace")
        self.fixed_width_id = self.fixed_width_button.connect("toggled",
            lambda w: self.on_fixed_width(True))
        toolbar.insert(self.fixed_width_button, -1)
        self.font_ui_signals.append(FontUI(self.fixed_width_button,
                                           self.fixed_width_id))

        # no wrap tool
        self.no_wrap_button = gtk.ToggleToolButton()
        self.no_wrap_button.set_icon_widget(get_resource_image("no-wrap.png"))
        tips.set_tip(self.no_wrap_button, "No Wrapping")
        self.no_wrap_id = self.no_wrap_button.connect("toggled",
            lambda w: self.editor.get_textview().toggle_font_mod("nowrap"))
        toolbar.insert(self.no_wrap_button, -1)
        self.font_ui_signals.append(FontUI(self.no_wrap_button,
                                           self.no_wrap_id))

        # family combo
        self.font_family_combo = FontSelector()
        self.font_family_combo.set_size_request(150, 25)
        item = gtk.ToolItem()
        item.add(self.font_family_combo)
        tips.set_tip(item, "Font Family")
        toolbar.insert(item, -1)
        self.font_family_id = self.font_family_combo.connect("changed",
            lambda w: self.on_family_set())
        self.font_ui_signals.append(FontUI(self.font_family_combo,
                                           self.font_family_id))
                
        # font size
        DEFAULT_FONT_SIZE = 10
        self.font_size_button = gtk.SpinButton(
          gtk.Adjustment(value=DEFAULT_FONT_SIZE, lower=2, upper=500, 
                         step_incr=1, page_incr=2))
        self.font_size_button.set_size_request(-1, 25)
        #self.font_size_button.set_range(2, 100)
        self.font_size_button.set_value(DEFAULT_FONT_SIZE)
        self.font_size_button.set_editable(False)
        item = gtk.ToolItem()
        item.add(self.font_size_button)
        tips.set_tip(item, "Font Size")
        toolbar.insert(item, -1)
        self.font_size_id = self.font_size_button.connect("value-changed",
            lambda w: 
            self.on_font_size_change(self.font_size_button.get_value()))
        self.font_ui_signals.append(FontUI(self.font_size_button,
                                           self.font_size_id))


        # font fg color
        # TODO: code in proper default color
        self.fg_color_button = FgColorTool(14, 15, (0, 0, 0))
        self.fg_color_button.connect("set-color",
                                     lambda w, color: self.on_color_set("fg",
                                                                     color))
        tips.set_tip(self.fg_color_button, "Set Text Color")
        toolbar.insert(self.fg_color_button, -1)
        

        # font bg color
        self.bg_color_button = BgColorTool(14, 15, (65535, 65535, 65535))
        self.bg_color_button.connect("set-color",
                                     lambda w, color: self.on_color_set("bg",
                                                                     color))
        tips.set_tip(self.bg_color_button, "Set Background Color")
        toolbar.insert(self.bg_color_button, -1)

                
        
        # separator
        toolbar.insert(gtk.SeparatorToolItem(), -1)
        
                
        # left tool
        self.left_button = gtk.ToggleToolButton()
        if self.app.pref.use_stock_icons:
            self.left_button.set_stock_id(gtk.STOCK_JUSTIFY_LEFT)
        else:
            self.left_button.set_icon_widget(
                get_resource_image("alignleft.png"))
        tips.set_tip(self.left_button, "Left Align")
        self.left_id = self.left_button.connect("toggled",
                                            lambda w: self.on_justify("left"))
        toolbar.insert(self.left_button, -1)
        self.font_ui_signals.append(FontUI(self.left_button,
                                           self.left_id))
        
        # center tool
        self.center_button = gtk.ToggleToolButton()
        if self.app.pref.use_stock_icons:
            self.center_button.set_stock_id(gtk.STOCK_JUSTIFY_CENTER)
        else:
            self.center_button.set_icon_widget(
                get_resource_image("aligncenter.png"))
        tips.set_tip(self.center_button, "Center Align")
        self.center_id = self.center_button.connect("toggled",
                                          lambda w: self.on_justify("center"))
        toolbar.insert(self.center_button, -1)
        self.font_ui_signals.append(FontUI(self.center_button,
                                           self.center_id))
        
        # right tool
        self.right_button = gtk.ToggleToolButton()
        if self.app.pref.use_stock_icons:
            self.right_button.set_stock_id(gtk.STOCK_JUSTIFY_RIGHT)
        else:
            self.right_button.set_icon_widget(
                get_resource_image("alignright.png"))
        tips.set_tip(self.right_button, "Right Align")
        self.right_id = self.right_button.connect("toggled",
                                           lambda w: self.on_justify("right"))
        toolbar.insert(self.right_button, -1)
        self.font_ui_signals.append(FontUI(self.right_button,
                                           self.right_id))
        
        # justify tool
        self.fill_button = gtk.ToggleToolButton()
        if self.app.pref.use_stock_icons:
            self.fill_button.set_stock_id(gtk.STOCK_JUSTIFY_FILL)
        else:
            self.fill_button.set_icon_widget(
                get_resource_image("alignjustify.png"))
        tips.set_tip(self.fill_button, "Justify Align")
        self.fill_id = self.fill_button.connect("toggled",
                                             lambda w: self.on_justify("fill"))
        toolbar.insert(self.fill_button, -1)
        self.font_ui_signals.append(FontUI(self.fill_button,
                                           self.fill_id))


        # bullet list tool
        self.bullet_button = gtk.ToggleToolButton()
        self.bullet_button.set_icon_widget(get_resource_image("bullet.png"))
        tips.set_tip(self.bullet_button, "Bullet List")
        self.bullet_id = self.bullet_button.connect("toggled",
                                            lambda w: self.on_bullet_list())
        toolbar.insert(self.bullet_button, -1)
        self.font_ui_signals.append(FontUI(self.bullet_button,
                                    self.bullet_id))


        # separator
        spacer = gtk.SeparatorToolItem()
        spacer.set_draw(False)
        spacer.set_expand(True)
        toolbar.insert(spacer, -1)


        # search box
        item = gtk.ToolItem()
        self.search_box = gtk.Entry()
        #self.search_box.set_max_chars(30)
        self.search_box.connect("activate",
                                lambda w: self.on_search_nodes())
        item.add(self.search_box)
        toolbar.insert(item, -1)

        # search button
        self.search_button = gtk.ToolButton()
        self.search_button.set_stock_id(gtk.STOCK_FIND)
        tips.set_tip(self.search_button, "Search Notes")
        self.search_button.connect("clicked",
                                   lambda w: self.on_search_nodes())
        toolbar.insert(self.search_button, -1)
        
                
        return toolbar



    def make_context_menus(self):
        """Initialize context menus"""        

        #==========================
        # image context menu
        item = gtk.SeparatorMenuItem()
        item.show()
        self.editor.get_textview().get_image_menu().append(item)
            
        # image/edit
        item = gtk.MenuItem("_View Image...")
        item.connect("activate", self.on_view_image)
        item.child.set_markup_with_mnemonic("<b>_View Image...</b>")
        item.show()
        self.editor.get_textview().get_image_menu().append(item)
        
        item = gtk.MenuItem("_Edit Image...")
        item.connect("activate", self.on_edit_image)
        item.show()
        self.editor.get_textview().get_image_menu().append(item)

        item = gtk.MenuItem("_Resize Image...")
        item.connect("activate", self.on_resize_image)
        item.show()
        self.editor.get_textview().get_image_menu().append(item)

        # image/save
        item = gtk.ImageMenuItem("_Save Image As...")
        item.connect("activate", self.on_save_image_as)
        item.show()
        self.editor.get_textview().get_image_menu().append(item)

        #===============================
        # treeview context menu
        # treeview/new folder
        item = gtk.ImageMenuItem()        
        item.set_image(get_resource_image("folder-new.png"))
        label = gtk.Label("New _Folder")
        label.set_use_underline(True)
        label.set_alignment(0.0, 0.5)
        label.show()
        item.add(label)
        item.connect("activate", lambda w: self.on_new_dir("treeview"))
        self.treeview.menu.append(item)
        item.show()
        
        # treeview/new page
        item = gtk.ImageMenuItem()
        item.set_image(get_resource_image("note-new.png"))        
        label = gtk.Label("New _Page")
        label.set_use_underline(True)
        label.set_alignment(0.0, 0.5)
        label.show()
        item.add(label)        
        item.connect("activate", lambda w: self.on_new_page("treeview"))
        self.treeview.menu.append(item)
        item.show()

        # treeview/delete node
        item = gtk.ImageMenuItem(gtk.STOCK_DELETE)
        item.connect("activate", lambda w: self.treeview.on_delete_node())
        self.treeview.menu.append(item)
        item.show()

        item = gtk.SeparatorMenuItem()
        self.treeview.menu.append(item)
        item.show()


        # treeview/file explorer
        item = gtk.MenuItem("View in File Explorer")
        item.connect("activate",
                     lambda w: self.on_view_node_external_app("file_explorer",
                                                              None,
                                                              "treeview"))
        self.treeview.menu.append(item)
        item.show()        

        # treeview/web browser
        item = gtk.MenuItem("View in Web Browser")
        item.connect("activate",
                     lambda w: self.on_view_node_external_app("web_browser",
                                                              None,
                                                             "treeview",
                                                              page_only=True))
        self.treeview.menu.append(item)
        item.show()        

        # treeview/text editor
        item = gtk.MenuItem("View in Text Editor")
        item.connect("activate",
                     lambda w: self.on_view_node_external_app("text_editor",
                                                              None,
                                                              "treeview",
                                                              page_only=True))
        self.treeview.menu.append(item)
        item.show()

        
        #=================================
        # listview context menu

        # listview/view note
        item = gtk.ImageMenuItem(gtk.STOCK_GO_DOWN)
        #item.child.set_label("Go to _Note")
        item.child.set_markup_with_mnemonic("<b>Go to _Note</b>")
        item.connect("activate",
                     lambda w: self.on_list_view_node(None, None))
        self.listview.menu.append(item)
        item.show()

        # listview/view note
        item = gtk.ImageMenuItem(gtk.STOCK_GO_UP)
        item.child.set_label("Go to _Parent Note")
        item.connect("activate",
                     lambda w: self.on_list_view_parent_node())
        self.listview.menu.append(item)
        item.show()

        item = gtk.SeparatorMenuItem()
        self.listview.menu.append(item)
        item.show()

        # listview/new folder
        item = gtk.ImageMenuItem()
        item.set_image(get_resource_image("folder-new.png"))
        label = gtk.Label("New _Folder")
        label.set_use_underline(True)
        label.set_alignment(0.0, 0.5)
        label.show()
        item.add(label)
        item.connect("activate", lambda w: self.on_new_dir("listview"))
        self.listview.menu.append(item)
        item.show()
        
        # listview/new page
        item = gtk.ImageMenuItem()
        item.set_image(get_resource_image("note-new.png"))        
        label = gtk.Label("New _Page")
        label.set_use_underline(True)
        label.set_alignment(0.0, 0.5)
        label.show()
        item.add(label)        
        item.connect("activate", lambda w: self.on_new_page("listview"))
        self.listview.menu.append(item)
        item.show()

        # listview/delete node
        item = gtk.ImageMenuItem(gtk.STOCK_DELETE)
        item.connect("activate", lambda w: self.listview.on_delete_page())
        self.listview.menu.append(item)
        item.show()

        item = gtk.SeparatorMenuItem()
        item.show()
        self.listview.menu.append(item)
        
        # listview/file explorer
        item = gtk.MenuItem("View in File _Explorer")
        item.connect("activate",
                     lambda w: self.on_view_node_external_app("file_explorer",
                                                              None,
                                                              "listview"))
        self.listview.menu.append(item)
        item.show()

        # listview/web browser
        item = gtk.MenuItem("View in _Web Browser")
        item.connect("activate",
                     lambda w: self.on_view_node_external_app("web_browser",
                                                              None,
                                                             "listview",
                                                              page_only=True))
        self.listview.menu.append(item)
        item.show()        

        # listview/text editor
        item = gtk.MenuItem("View in _Text Editor")
        item.connect("activate",
                     lambda w: self.on_view_node_external_app("text_editor",
                                                              None,
                                                              "listview",
                                                              page_only=True))
        self.listview.menu.append(item)
        item.show()        






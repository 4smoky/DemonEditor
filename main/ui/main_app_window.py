from contextlib import suppress
from threading import Thread

from main.eparser import get_channels, get_bouquets, write_bouquet, write_channels
from main.ftp import download_data, upload_data
from main.properties import get_config, write_config
from . import Gtk, Gdk
from .satellites_dialog import show_satellites_dialog
from .settings_dialog import show_settings_dialog

SERVICE_LIST_NAME = "services_list_store"
FAV_LIST_NAME = "fav_list_store"
BOUQUETS_LIST_NAME = "bouquets_tree_store"

__main_window = None
__status_bar = None
__options = get_config()
__services_model = None
__bouquets_model = None
__fav_model = None
__services_view = None
__fav_view = None
__bouquets_view = None
# Used for copy/paste
# When adding the previous data will not be deleted.
# Clearing only after the insertion!
__rows_buffer = []
__channels = {}
__bouquets = {}


def on_about_app(item):
    builder = Gtk.Builder()
    builder.add_from_file("ui/main_window.glade")
    dialog = builder.get_object("about_dialog")
    dialog.run()
    dialog.destroy()


def get_handlers():
    return {
        "on_close_main_window": on_quit,
        "on_resize": on_resize,
        "on_about_app": on_about_app,
        "on_preferences": on_preferences,
        "on_download": on_download,
        "on_upload": on_upload,
        "on_data_open": on_data_open,
        "on_data_save": on_data_save,
        "on_tree_view_key_release": on_tree_view_key_release,
        "on_bouquets_selection": on_bouquets_selection,
        "on_satellite_editor_show": on_satellite_editor_show,
        "on_services_selection": on_services_selection,
        "on_fav_selection": on_fav_selection,
        "on_up": on_up,
        "on_down": on_down,
        "on_cut": on_cut,
        "on_copy": on_copy,
        "on_paste": on_paste,
        "on_delete": on_delete,
        "on_to_fav_move": on_to_fav_move,
        "on_services_tree_view_drag_data_get": on_services_tree_view_drag_data_get,
        "on_fav_tree_view_drag_data_get": on_fav_tree_view_drag_data_get,
        "on_fav_tree_view_drag_data_received": on_fav_tree_view_drag_data_received,
        "on_view_popup_menu": on_view_popup_menu
    }


def on_quit(*args):
    """  Called before app quit """
    write_config(__options)  # storing current config
    Gtk.main_quit()


def on_resize(window):
    """ Stores new size properties for main window after resize """
    __options["window_size"] = window.get_size()


def on_up(item):
    pass


def on_down(item):
    pass


def on_cut(view):
    for row in on_delete(view):
        __rows_buffer.append(row)


def on_copy(item):
    pass


def on_paste(view):
    selection = view.get_selection()
    dest_index = 0
    bq_selected = is_bouquet_selected()
    if bq_selected:
        fav_bouquet = __bouquets[bq_selected]
    model, paths = selection.get_selected_rows()
    if paths:
        dest_index = int(paths[0][0])
    for row in __rows_buffer:
        dest_index += 1
        model.insert(dest_index, row)
        fav_bouquet.insert(dest_index, row[-1])
    if model.get_name() == FAV_LIST_NAME:
        update_fav_num_column(model)
    __rows_buffer.clear()


def on_delete(item):
    """ Delete selected items from views

        returns deleted rows list!
    """
    for view in [__services_view, __fav_view, __bouquets_view]:
        if view.is_focus():
            selection = view.get_selection()
            model, paths = selection.get_selected_rows()
            model_name = model.get_name()
            itrs = [model.get_iter(path) for path in paths]
            rows = [model.get(in_itr, *[x for x in range(view.get_n_columns())]) for in_itr in itrs]
            bq_selected = is_bouquet_selected()
            fav_bouquet = None
            if bq_selected:
                fav_bouquet = __bouquets[bq_selected]
            for itr in itrs:
                if fav_bouquet and model_name == FAV_LIST_NAME:
                    del fav_bouquet[int(model.get_path(itr)[0])]
                if model_name == BOUQUETS_LIST_NAME:
                    if model.iter_has_child(itr):
                        show_message_dialog("This item is not allowed to be removed!")
                        return
                    else:
                        __bouquets.pop(bq_selected)
                model.remove(itr)
            if model_name == FAV_LIST_NAME:
                update_fav_num_column(model)
            if model_name == SERVICE_LIST_NAME:
                for row in rows:
                    # There are channels with the same parameters except for the name.
                    # None because it can have duplicates! Need fix
                    fav_id = row[-1]
                    for bq in __bouquets:
                        services = __bouquets[bq]
                        with suppress(ValueError):
                            services.remove(fav_id)
                    __channels.pop(fav_id, None)
                __fav_model.clear()
                update_bouquet_channels(__fav_model, None, bq_selected)
            return rows


def on_to_fav_move(view):
    """ Move items from main to fav list """
    selection = get_selection(view)
    if selection:
        receive_selection(view=__fav_view, drop_info=None, data=selection)


def get_selection(view):
    """ Creates a string from the iterators of the selected rows """
    selection = view.get_selection()
    model, paths = selection.get_selected_rows()
    if len(paths) > 0:
        itrs = [model.get_iter(path) for path in paths]
        return "{}:{}".format(",".join([model.get_string_from_iter(itr) for itr in itrs]), model.get_name())


def receive_selection(*, view, drop_info, data):
    """  Update fav view  after data received  """
    bq_selected = is_bouquet_selected()
    if not bq_selected:
        show_message_dialog("Error. No bouquet is selected!")
        return
    model = view.get_model()
    dest_index = 0
    if drop_info:
        path, position = drop_info
        dest_iter = model.get_iter(path)
        if dest_iter:
            dest_index = model.get_value(dest_iter, 0)
    itr_str, sep, source = data.partition(":")
    itrs = itr_str.split(",")
    try:
        fav_bouquet = __bouquets[bq_selected]
        if source == SERVICE_LIST_NAME:
            ext_model = __services_view.get_model()
            ext_itrs = [ext_model.get_iter_from_string(itr) for itr in itrs]
            ext_rows = [ext_model.get(ext_itr, *[x for x in range(__services_view.get_n_columns())]) for ext_itr in
                        ext_itrs]
            dest_index -= 1
            for ext_row in ext_rows:
                dest_index += 1
                fav_id = ext_row[11]
                channel = __channels[fav_id]
                model.insert(dest_index, (0, channel.service, channel.service_type, channel.pos, channel.fav_id))
                fav_bouquet.insert(dest_index, channel.fav_id)
        elif source == FAV_LIST_NAME:
            in_itrs = [model.get_iter_from_string(itr) for itr in itrs]
            in_rows = [model.get(in_itr, *[x for x in range(view.get_n_columns())]) for in_itr in in_itrs]
            for row in in_rows:
                model.insert(dest_index, row)
                fav_bouquet.insert(dest_index, row[4])
            for in_itr in in_itrs:
                del fav_bouquet[int(model.get_path(in_itr)[0])]
                model.remove(in_itr)
        update_fav_num_column(model)
    except ValueError as e:
        __status_bar.push(1, getattr(e, "message", repr(e)))


def update_fav_num_column(model):
    """ Iterate through model and updates values for Num column """
    model.foreach(lambda store, pth, itr: store.set_value(itr, 0, int(pth[0]) + 1))  # iter , column, value


def on_services_tree_view_drag_data_get(view, drag_context, data, info, time):
    """  DnD  """
    data.set_text(get_selection(view), -1)


def on_fav_tree_view_drag_data_get(view, drag_context, data, info, time):
    """ DnD """
    data.set_text(get_selection(view), -1)


def on_fav_tree_view_drag_data_received(view, drag_context, x, y, data, info, time):
    """ DnD """
    receive_selection(view=view, drop_info=view.get_dest_row_at_pos(x, y), data=data.get_text())


def on_view_popup_menu(menu, event):
    """ Shows popup menu for any view """
    if event.get_event_type() == Gdk.EventType.BUTTON_PRESS and event.button == Gdk.BUTTON_SECONDARY:
        menu.popup(None, None, None, None, event.button, event.time)


def on_satellite_editor_show(model):
    """ Shows satellites editor dialog """
    show_satellites_dialog(__main_window, __options["data_dir_path"])


def data_open(model):
    try:
        model.clear()
        __fav_model.clear()
        model_name = model.get_name()
        data_path = get_config()["data_dir_path"]
        if model_name == SERVICE_LIST_NAME:
            for ch in get_channels(data_path + "lamedb"):
                #  adding channels to dict with fav_id as keys
                __channels[ch.fav_id] = ch
                model.append(ch)
        if model_name == BOUQUETS_LIST_NAME:
            bouquets = get_bouquets(data_path)
            for bouquet in bouquets:
                parent = model.append(None, [bouquet.name, None])
                for bt in bouquet.bouquets:
                    name, bt_type = bt.name, bt.type
                    model.append(parent, [name, bt_type])
                    __bouquets["{}:{}".format(name, bt_type)] = bt.services
    except Exception as e:
        __status_bar.push(1, getattr(e, "message", repr(e)))


def on_data_open(model):
    # Maybe is not necessary? Need testing.
    task = Thread(target=data_open(model))
    task.start()


def on_data_save(*args):
    #  Perhaps needs a dialog to choose what we need to save!!!
    bouquet_selected = is_bouquet_selected()
    path = __options["data_dir_path"]
    if bouquet_selected and __fav_view.is_focus():  # bouquets
        fav_ids = []
        __fav_model.foreach(lambda model, p, itr: fav_ids.append(model.get(model.get_iter(p), 4)))
        channels = [__channels[fav_id[0]] for fav_id in fav_ids]
        write_bouquet(path, bouquet_selected, channels)
    elif __services_view.is_focus():
        write_channels(path, __channels.values())


def on_services_selection(model, path, column):
    delete_selection(__fav_view)


def on_fav_selection(model, path, column):
    delete_selection(__services_view)


def on_bouquets_selection(model, path, column):
    __fav_model.clear()
    if len(path) > 1:
        delete_selection(__services_view)
        update_bouquet_channels(model, path)


def update_bouquet_channels(model, path, bq_key=None):
    """ Updates list of bouquet channels """
    tree_iter = None
    if path:
        tree_iter = model.get_iter(path)
    key = bq_key if bq_key else "{}:{}".format(*model.get(tree_iter, 0, 1))
    services = __bouquets[key]
    for num, ch_id in enumerate(services):
        channel = __channels.get(ch_id, None)
        if channel:
            __fav_model.append((num + 1, channel.service, channel.service_type, channel.pos, channel.fav_id))


def is_bouquet_selected():
    """ Checks whether the bouquet is selected

        returns 'name:type' of selected bouquet or False
    """
    selection = __bouquets_view.get_selection()
    model, path = selection.get_selected_rows()
    if len(path) < 1 or model.iter_has_child(model.get_iter(path)):
        return False
    return "{}:{}".format(*model.get(model.get_iter(path), 0, 1))


def show_message_dialog(text):
    builder = Gtk.Builder()
    builder.add_from_file("ui/main_window.glade")
    dialog = builder.get_object("message_dialog")
    dialog.set_markup(text)
    dialog.run()
    dialog.destroy()


def delete_selection(view, *args):
    """ Used for clear selection on given view(s) """
    for v in [view, *args]:
        v.get_selection().unselect_all()


def on_preferences(item):
    show_settings_dialog(__main_window, __options)


def on_tree_view_key_release(view, event):
    """  Handling  keystrokes  """
    key = event.keyval
    ctrl = event.state & Gdk.ModifierType.CONTROL_MASK
    model_name = view.get_model().get_name()
    if key == Gdk.KEY_Delete:
        on_delete(view)
    elif key == Gdk.KEY_Up:
        print("Up")
    elif key == Gdk.KEY_Down:
        print("Down")
    elif key == Gdk.KEY_Insert and view.get_model().get_name() == SERVICE_LIST_NAME:
        # Move items from main to fav list
        on_to_fav_move(view)
    elif ctrl and key == Gdk.KEY_x or key == Gdk.KEY_X:
        if model_name == FAV_LIST_NAME:
            on_cut(view)
    elif ctrl and key == Gdk.KEY_v or key == Gdk.KEY_V:
        on_paste(view)


def on_upload(item):
    connect(__options, False)


def on_download(item):
    connect(__options)


def on_reload(item):
    pass


def connect(properties, download=True):
    try:
        res = download_data(properties=properties) if download else upload_data(properties=properties)
        __status_bar.push(1, res)
    except Exception as e:
        __status_bar.remove_all(1)
        __status_bar.push(1, getattr(e, "message", repr(e)))  # Or maybe so: getattr(e, 'message', str(e))


def init_ui():
    builder = Gtk.Builder()
    builder.add_from_file("ui/main_window.glade")
    global __main_window
    __main_window = builder.get_object("main_window")
    main_window_size = __options.get("window_size", None)
    # Setting the last size of the window if it was saved
    if main_window_size:
        __main_window.resize(*main_window_size)
    global __services_view
    __services_view = builder.get_object("services_tree_view")
    global __fav_view
    __fav_view = builder.get_object("fav_tree_view")
    global __bouquets_view
    __bouquets_view = builder.get_object("bouquets_tree_view")
    global __fav_model
    __fav_model = builder.get_object("fav_list_store")
    global __status_bar
    __status_bar = builder.get_object("status_bar")
    builder.connect_signals(get_handlers())
    init_drag_and_drop()  # drag and drop
    __main_window.show_all()


def init_drag_and_drop():
    """ Enable drag and drop """
    target = []
    __services_view.enable_model_drag_source(Gdk.ModifierType.BUTTON1_MASK, target, Gdk.DragAction.COPY)
    __fav_view.enable_model_drag_source(Gdk.ModifierType.BUTTON1_MASK, target,
                                        Gdk.DragAction.DEFAULT | Gdk.DragAction.MOVE)
    __fav_view.enable_model_drag_dest(target, Gdk.DragAction.DEFAULT | Gdk.DragAction.MOVE)
    __fav_view.drag_dest_set_target_list(None)
    __fav_view.drag_source_set_target_list(None)
    __fav_view.drag_dest_add_text_targets()
    __fav_view.drag_source_add_text_targets()
    __services_view.drag_source_set_target_list(None)
    __services_view.drag_source_add_text_targets()


def start_app():
    init_ui()
    Gtk.main()


def close_app():
    Gtk.main_quit()


if __name__ == "__main__":
    start_app()

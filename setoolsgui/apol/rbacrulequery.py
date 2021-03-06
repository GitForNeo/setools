# Copyright 2015, Tresys Technology, LLC
#
# This file is part of SETools.
#
# SETools is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as
# published by the Free Software Foundation, either version 2.1 of
# the License, or (at your option) any later version.
#
# SETools is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with SETools.  If not, see
# <http://www.gnu.org/licenses/>.
#

import logging

from PyQt5.QtCore import Qt, QSortFilterProxyModel, QStringListModel, QThread
from PyQt5.QtGui import QPalette, QTextCursor
from PyQt5.QtWidgets import QCompleter, QHeaderView, QMessageBox, QProgressDialog, QScrollArea
from setools import RBACRuleQuery

from ..logtosignal import LogHandlerToSignal
from ..models import SEToolsListModel, invert_list_selection
from ..rbacrulemodel import RBACRuleTableModel
from ..widget import SEToolsWidget
from .queryupdater import QueryResultsUpdater
from .workspace import load_checkboxes, load_lineedits, load_listviews, load_textedits, \
                       save_checkboxes, save_lineedits, save_listviews, save_textedits


class RBACRuleQueryTab(SEToolsWidget, QScrollArea):

    """A RBAC rule query."""

    def __init__(self, parent, policy, perm_map):
        super(RBACRuleQueryTab, self).__init__(parent)
        self.log = logging.getLogger(__name__)
        self.policy = policy
        self.query = RBACRuleQuery(policy)
        self.setupUi()

    def __del__(self):
        self.thread.quit()
        self.thread.wait(5000)
        logging.getLogger("setools.rbacrulequery").removeHandler(self.handler)

    def setupUi(self):
        self.load_ui("rbacrulequery.ui")

        # set up role autocompletion (source, default)
        role_completion_list = [str(r) for r in self.policy.roles()]
        role_completer_model = QStringListModel(self)
        role_completer_model.setStringList(sorted(role_completion_list))
        self.role_completion = QCompleter()
        self.role_completion.setModel(role_completer_model)
        self.source.setCompleter(self.role_completion)
        self.default_role.setCompleter(self.role_completion)

        # set up role/type autocompletion (target)
        roletype_completion_list = [str(r) for r in self.policy.roles()]
        # roletype_completion_list.extend(str(a) for a in self.policy.roleattributes())
        roletype_completion_list.extend(str(t) for t in self.policy.types())
        roletype_completion_list.extend(str(a) for a in self.policy.typeattributes())
        roletype_completer_model = QStringListModel(self)
        roletype_completer_model.setStringList(sorted(roletype_completion_list))
        self.roletype_completion = QCompleter()
        self.roletype_completion.setModel(roletype_completer_model)
        self.target.setCompleter(self.roletype_completion)

        # setup indications of errors on source/target/default
        self.orig_palette = self.source.palette()
        self.error_palette = self.source.palette()
        self.error_palette.setColor(QPalette.Base, Qt.red)
        self.clear_source_error()
        self.clear_target_error()
        self.clear_default_error()

        # populate class list
        self.class_model = SEToolsListModel(self)
        self.class_model.item_list = sorted(self.policy.classes())
        self.tclass.setModel(self.class_model)

        # set up results
        self.table_results_model = RBACRuleTableModel(self)
        self.sort_proxy = QSortFilterProxyModel(self)
        self.sort_proxy.setSourceModel(self.table_results_model)
        self.table_results.setModel(self.sort_proxy)
        self.table_results.sortByColumn(0, Qt.AscendingOrder)

        # set up processing thread
        self.thread = QThread()
        self.worker = QueryResultsUpdater(self.query, self.table_results_model)
        self.worker.moveToThread(self.thread)
        self.worker.raw_line.connect(self.raw_results.appendPlainText)
        self.worker.finished.connect(self.update_complete)
        self.worker.finished.connect(self.thread.quit)
        self.thread.started.connect(self.worker.update)

        # create a "busy, please wait" dialog
        self.busy = QProgressDialog(self)
        self.busy.setModal(True)
        self.busy.setRange(0, 0)
        self.busy.setMinimumDuration(0)
        self.busy.canceled.connect(self.thread.requestInterruption)
        self.busy.reset()

        # update busy dialog from query INFO logs
        self.handler = LogHandlerToSignal()
        self.handler.message.connect(self.busy.setLabelText)
        logging.getLogger("setools.rbacrulequery").addHandler(self.handler)

        # Ensure settings are consistent with the initial .ui state
        self.set_source_regex(self.source_regex.isChecked())
        self.set_target_regex(self.target_regex.isChecked())
        self.set_default_regex(self.default_regex.isChecked())
        self.criteria_frame.setHidden(not self.criteria_expander.isChecked())
        self.notes.setHidden(not self.notes_expander.isChecked())

        # connect signals
        self.buttonBox.clicked.connect(self.run)
        self.clear_ruletypes.clicked.connect(self.clear_all_ruletypes)
        self.all_ruletypes.clicked.connect(self.set_all_ruletypes)
        self.source.textEdited.connect(self.clear_source_error)
        self.source.editingFinished.connect(self.set_source)
        self.source_regex.toggled.connect(self.set_source_regex)
        self.target.textEdited.connect(self.clear_target_error)
        self.target.editingFinished.connect(self.set_target)
        self.target_regex.toggled.connect(self.set_target_regex)
        self.tclass.selectionModel().selectionChanged.connect(self.set_tclass)
        self.invert_class.clicked.connect(self.invert_tclass_selection)
        self.default_role.textEdited.connect(self.clear_default_error)
        self.default_role.editingFinished.connect(self.set_default_role)
        self.default_regex.toggled.connect(self.set_default_regex)

    #
    # Ruletype criteria
    #

    def _set_ruletypes(self, value):
        self.allow.setChecked(value)
        self.role_transition.setChecked(value)

    def set_all_ruletypes(self):
        self._set_ruletypes(True)

    def clear_all_ruletypes(self):
        self._set_ruletypes(False)

    #
    # Source criteria
    #

    def clear_source_error(self):
        self.source.setToolTip("Match the source role of the rule.")
        self.source.setPalette(self.orig_palette)

    def set_source(self):
        try:
            self.query.source = self.source.text()
        except Exception as ex:
            self.log.error("Source role error: {0}".format(ex))
            self.source.setToolTip("Error: " + str(ex))
            self.source.setPalette(self.error_palette)

    def set_source_regex(self, state):
        self.log.debug("Setting source_regex {0}".format(state))
        self.query.source_regex = state
        self.clear_source_error()
        self.set_source()

    #
    # Target criteria
    #

    def clear_target_error(self):
        self.target.setToolTip("Match the target role/type of the rule.")
        self.target.setPalette(self.orig_palette)

    def set_target(self):
        try:
            self.query.target = self.target.text()
        except Exception as ex:
            self.log.error("Target type/role error: {0}".format(ex))
            self.target.setToolTip("Error: " + str(ex))
            self.target.setPalette(self.error_palette)

    def set_target_regex(self, state):
        self.log.debug("Setting target_regex {0}".format(state))
        self.query.target_regex = state
        self.clear_target_error()
        self.set_target()

    #
    # Class criteria
    #

    def set_tclass(self):
        selected_classes = []
        for index in self.tclass.selectionModel().selectedIndexes():
            selected_classes.append(self.class_model.data(index, Qt.UserRole))

        self.query.tclass = selected_classes

    def invert_tclass_selection(self):
        invert_list_selection(self.tclass.selectionModel())

    #
    # Default criteria
    #

    def clear_default_error(self):
        self.default_role.setToolTip("Match the default role the rule.")
        self.default_role.setPalette(self.orig_palette)

    def set_default_role(self):
        self.query.default_regex = self.default_regex.isChecked()

        try:
            self.query.default = self.default_role.text()
        except Exception as ex:
            self.log.error("Default role error: {0}".format(ex))
            self.default_role.setToolTip("Error: " + str(ex))
            self.default_role.setPalette(self.error_palette)

    def set_default_regex(self, state):
        self.log.debug("Setting default_regex {0}".format(state))
        self.query.default_regex = state
        self.clear_default_error()
        self.set_default_role()

    #
    # Save/Load tab
    #
    def save(self):
        """Return a dictionary of settings."""
        settings = {}
        save_checkboxes(self, settings, ["criteria_expander", "notes_expander",
                                         "allow", "role_transition",
                                         "source_indirect", "source_regex",
                                         "target_indirect", "target_regex",
                                         "default_regex"])

        save_lineedits(self, settings, ["source", "target", "default_role"])

        save_listviews(self, settings, ["tclass"])

        save_textedits(self, settings, ["notes"])

        return settings

    def load(self, settings):
        load_checkboxes(self, settings, ["criteria_expander", "notes_expander",
                                         "allow", "role_transition",
                                         "source_indirect", "source_regex",
                                         "target_indirect", "target_regex",
                                         "default_regex"])

        load_lineedits(self, settings, ["source", "target", "default_role"])

        load_listviews(self, settings, ["tclass"])

        load_textedits(self, settings, ["notes"])

    #
    # Results runner
    #

    def run(self, button):
        # right now there is only one button.
        rule_types = []

        for mode in [self.allow, self.role_transition]:
            if mode.isChecked():
                rule_types.append(mode.objectName())

        self.query.ruletype = rule_types
        self.query.source_indirect = self.source_indirect.isChecked()
        self.query.target_indirect = self.target_indirect.isChecked()

        # start processing
        self.busy.setLabelText("Processing query...")
        self.busy.show()
        self.raw_results.clear()
        self.thread.start()

    def update_complete(self, count):
        self.log.info("{0} RBAC rule(s) found.".format(count))

        # update sizes/location of result displays
        if not self.busy.wasCanceled():
            self.busy.setLabelText("Resizing the result table's columns; GUI may be unresponsive")
            self.busy.repaint()
            self.table_results.resizeColumnsToContents()

        if not self.busy.wasCanceled():
            self.busy.setLabelText("Resizing the result table's rows; GUI may be unresponsive")
            self.busy.repaint()
            self.table_results.resizeRowsToContents()

        if not self.busy.wasCanceled():
            self.busy.setLabelText("Moving the raw result to top; GUI may be unresponsive")
            self.busy.repaint()
            self.raw_results.moveCursor(QTextCursor.Start)

        self.busy.reset()

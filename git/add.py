from __future__ import absolute_import, unicode_literals, print_function, division

import os
import re

import sublime
from . import GitTextCommand, GitWindowCommand, git_root
from .status import GitStatusCommand


class GitAddChoiceCommand(GitStatusCommand):
    def status_filter(self, item):
        return super(GitAddChoiceCommand, self).status_filter(item) and not item[1].isspace()

    def show_status_list(self):
        self.results = [
            [" + All Files", "apart from untracked files"],
            [" + All Files", "including untracked files"],
        ] + [[a, ''] for a in self.results]
        return super(GitAddChoiceCommand, self).show_status_list()

    def panel_followup(self, picked_status, picked_file, picked_index):
        working_dir = git_root(self.get_working_dir())

        if picked_index == 0:
            command = ['git', 'add', '--update']
        elif picked_index == 1:
            command = ['git', 'add', '--all']
        else:
            command = ['git']
            picked_file = picked_file.strip('"')
            if os.path.exists(working_dir + "/" + picked_file):
                command += ['add']
            else:
                command += ['rm']
            command += ['--', picked_file]

        self.run_command(
            command, self.rerun,
            working_dir=working_dir
        )

    def rerun(self, result):
        self.run()

from .diff import get_gitDiffRootInView

class GitAddSelectedHunkCommand(GitTextCommand):

    def is_gitDiffView(self, view):
        return view.name() == "Git Diff" and get_gitDiffRootInView(view) is not None

    def is_enabled(self):

        view = self.active_view()
        if self.is_gitDiffView(view):
            return True

        # First, is this actually a file on the file system?
        return super().is_enabled()

    def searchDiffLines(self, sel) :
        matcher = re.compile('@@ -([0-9]*)(?:,([0-9]*))? \+([0-9]*)(?:,([0-9]*))? @@')
        line = self.view.substr(sel)
        #print("line_region:", sel, line)
        match = matcher.search(line)
        if match:
            start = match.group(3)
            end = match.group(4)
            return int(start), int(end)
        return None, None

    def searchGitDiff(self, sel) :
        matcher = re.compile('--- a/(.+)\n\+\+\+ b/(.+)\n@@ -([0-9]*)(?:,([0-9]*))? \+([0-9]*)(?:,([0-9]*))? @@')
        line = self.view.substr(sel)
        #print("line_region:", sel, line)
        match = matcher.search(line)
        if match:
            file1 = match.group(1)
            file2 = match.group(2)
            if file1 == file2:
                start = match.group(5)
                end = match.group(6)
                return file1, int(start), int(end)
        return None, None, None

    def getGitDiffSelection(self):

        no_context_lines_diff = 2
        fileSelectionD = {}
        for region in self.view.sel():
            expandedRegion = region
            fileName = None
            start = None

            while 0 < expandedRegion.begin():

                if start is None:
                    start, size = self.searchDiffLines(expandedRegion)

                if start is not None:
                    fileName, x, y = self.searchGitDiff(expandedRegion)
                    if fileName:
                        selection = {
                            "start": start+no_context_lines_diff,
                            "end": start+size-no_context_lines_diff,
                        }
                        if fileName not in fileSelectionD:
                            fileSelectionD[fileName] = []
                        fileSelectionD[fileName].append(selection)
                        break

                expandedRegion = sublime.Region(expandedRegion.begin()-1,expandedRegion.end())

        return fileSelectionD

    def getViewSelection(self):
        selection = []
        for sel in self.view.sel():
            selection.append({
                "start": self.view.rowcol(sel.begin())[0] + 1,
                "end": self.view.rowcol(sel.end())[0] + 1,
            })
        fileSelectionD = {self.get_file_name():selection}
        return fileSelectionD


    def run(self, edit, edit_patch=False):
        kwargs = {}
        if self.is_gitDiffView(self.view):
            # When getting the patch from a "Git Diff" file, the git_root of
            # the diff might not be the same as the git root of this view.
            kwargs['working_dir'] = get_gitDiffRootInView(self.view)
            fileSelectionD = self.getGitDiffSelection()
        else:
            fileSelectionD = self.getViewSelection()

        # TODO: we can't get patches for multiple files with one command yet
        for fileName in fileSelectionD:
            selectionL = fileSelectionD[fileName]
            self.run_command(['git', 'diff', '--no-color', '-U1', fileName], lambda result: self.cull_diff(result, selectionL, edit_patch, **kwargs), **kwargs)

    def cull_diff(self, result, selection, edit_patch=False, **kwargs):

        hunks = [{"diff": ""}]
        i = 0
        matcher = re.compile('^@@ -([0-9]*)(?:,([0-9]*))? \+([0-9]*)(?:,([0-9]*))? @@')
        for line in result.splitlines(keepends=True): # if different line endings, patch will not apply
            if line.startswith('@@'):
                i += 1
                match = matcher.match(line)
                start = int(match.group(3))
                end = match.group(4)
                if end:
                    end = start + int(end)
                else:
                    end = start
                hunks.append({"diff": "", "start": start, "end": end})
            hunks[i]["diff"] += line

        diffs = hunks[0]["diff"]
        hunks.pop(0)
        selection_is_hunky = False
        for hunk in hunks:
            for sel in selection:
                if sel["end"] < hunk["start"]:
                    continue
                if sel["start"] > hunk["end"]:
                    continue
                diffs += hunk["diff"]  # + "\n\nEND OF HUNK\n\n"
                selection_is_hunky = True

        if selection_is_hunky:
            if edit_patch: # open an input panel to modify the patch
                patch_view = self.get_window().show_input_panel(
                    "Message", diffs,
                    lambda edited_patch: self.on_input(edited_patch,**kwargs), None, None
                )
                s = sublime.load_settings("Git.sublime-settings")
                syntax = s.get("diff_syntax", "Packages/Diff/Diff.tmLanguage")
                patch_view.set_syntax_file(syntax)
                patch_view.settings().set('word_wrap', False)
            else:
                self.on_input(diffs,**kwargs)
        else:
            sublime.status_message("No selected hunk")

    def on_input(self, patch, **kwargs):
        self.run_command(['git', 'apply', '--cached'], stdin=patch, **kwargs)


# Also, sometimes we want to undo adds


class GitResetHead(object):
    def run(self, edit=None):
        self.run_command(['git', 'reset', 'HEAD', self.get_file_name()])

    def generic_done(self, result):
        pass


class GitResetHeadCommand(GitResetHead, GitTextCommand):
    pass


class GitResetHeadAllCommand(GitResetHead, GitWindowCommand):
    pass


class GitResetHardHeadCommand(GitWindowCommand):
    may_change_files = True

    def run(self):
        if sublime.ok_cancel_dialog("Warning: this will reset your index and revert all files, throwing away all your uncommitted changes with no way to recover. Consider stashing your changes instead if you'd like to set them aside safely.", "Continue"):
            self.run_command(['git', 'reset', '--hard', 'HEAD'])

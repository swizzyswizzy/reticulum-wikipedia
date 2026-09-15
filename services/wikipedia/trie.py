"""In-memory prefix tree for Wikipedia titles.

Each typed character walks one edge. Completions are collected from the
subtree under the current prefix — cheap to narrow as the query grows.
"""

from __future__ import annotations


class TrieNode:
    __slots__ = ("kids", "end", "title")

    def __init__(self):
        self.kids: dict[str, TrieNode] = {}
        self.end: bool = False
        self.title: str = ""


class TitleTrie:
    def __init__(self):
        self.root = TrieNode()
        self.size = 0

    def _walk(self, text: str):
        node = self.root
        for ch in text.casefold():
            nxt = node.kids.get(ch)
            if nxt is None:
                return None
            node = nxt
        return node

    def insert(self, title: str) -> None:
        title = " ".join((title or "").split())
        if not title:
            return
        node = self.root
        for ch in title.casefold():
            nxt = node.kids.get(ch)
            if nxt is None:
                nxt = TrieNode()
                node.kids[ch] = nxt
            node = nxt
        if not node.end:
            self.size += 1
        node.end = True
        node.title = title

    def contains(self, title: str) -> bool:
        node = self._walk((title or "").strip())
        return bool(node and node.end)

    def complete(self, prefix: str, limit: int = 20) -> list[str]:
        """Titles that start with prefix, plus a few in-title hits if sparse."""
        prefix = (prefix or "").strip()
        if not prefix:
            return []
        node = self._walk(prefix)
        out: list[str] = []
        if node is not None:
            self._collect(node, out, limit)
        if len(out) < min(6, limit):
            self._scan_contains(self.root, prefix.casefold(), out, limit)
        needle = prefix.casefold()

        def rank(t):
            low = t.casefold()
            if low == needle:
                return (0, 0, len(t), low)
            if low.startswith(needle):
                return (1, 0, len(t), low)
            words = low.replace("-", " ").replace(",", " ").split()
            if any(w.startswith(needle) for w in words):
                return (2, 0, len(t), low)
            return (3, low.find(needle), len(t), low)

        out.sort(key=rank)
        return out

    def _collect(self, node: TrieNode, out: list[str], limit: int) -> None:
        if len(out) >= limit:
            return
        if node.end and node.title not in out:
            out.append(node.title)
        for ch in sorted(node.kids):
            self._collect(node.kids[ch], out, limit)
            if len(out) >= limit:
                return

    def _scan_contains(self, node: TrieNode, needle: str, out: list[str], limit: int) -> None:
        if len(out) >= limit:
            return
        if node.end and needle in node.title.casefold() and node.title not in out:
            out.append(node.title)
        for ch in node.kids:
            self._scan_contains(node.kids[ch], needle, out, limit)
            if len(out) >= limit:
                return

    def load_lines(self, lines) -> int:
        before = self.size
        for line in lines:
            line = line.strip()
            if line and not line.startswith("#"):
                self.insert(line)
        return self.size - before

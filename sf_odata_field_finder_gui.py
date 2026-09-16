#!/usr/bin/env python3
"""Interfaz gráfica para explorar metadata SAP SuccessFactors OData V2 local o remoto."""

from __future__ import annotations

import argparse
import json
import os
import queue
import tkinter as tk
from datetime import datetime
from pathlib import Path
from threading import Event, Thread
from tkinter import filedialog, font as tkfont, messagebox, ttk
from typing import Callable, Iterable, Optional

from sf_odata_field_finder import (
    MetadataModel,
    build_select_expand,
    download_metadata,
    filter_metadata_results,
    find_entity,
    global_property_search,
    list_navigation_tree,
    parse_metadata,
    recursive_navigation_search,
    result_odata_route,
    search_odata_path_pattern,
    search_direct_properties,
    search_entities,
    search_properties_by_attribute,
    search_related_entities,
)


APP_TITLE = "SF OData Field Finder"
SEARCH_MODES = (
    "Nombre de propiedad",
    "Tipo OData",
    "sap:picklist",
    "Etiqueta sap:label",
    "Ruta / patrón OData",
    "Entidad relacionada",
    "Navegaciones de la raíz",
)


def format_size(value: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{value} B"
        value /= 1024
    return f"{value:.1f} GB"


def create_placeholder_entry(parent: tk.Misc, textvariable: tk.StringVar,
                             placeholder: str, **entry_options: object) -> tuple[ttk.Frame, ttk.Entry]:
    """Crea un Entry con texto de ejemplo sin guardarlo como valor real."""
    wrapper = ttk.Frame(parent)
    entry = ttk.Entry(wrapper, textvariable=textvariable, **entry_options)
    entry.pack(fill="both", expand=True)
    hint = ttk.Label(wrapper, text=placeholder, style="Placeholder.TLabel")

    def update_hint(*_args: object) -> None:
        if textvariable.get() or entry.focus_get() == entry:
            hint.place_forget()
        else:
            hint.place(x=7, rely=0.5, anchor="w")

    def focus_entry(_event: tk.Event) -> str:
        entry.focus_set()
        return "break"

    textvariable.trace_add("write", update_hint)
    entry.bind("<FocusIn>", update_hint)
    entry.bind("<FocusOut>", update_hint)
    hint.bind("<Button-1>", focus_entry)
    update_hint()
    return wrapper, entry


class MetadataWindow(tk.Toplevel):
    """Ventana independiente para descargar o seleccionar un metadata local."""

    def __init__(self, app: "ODataFinderApp") -> None:
        super().__init__(app)
        self.app = app
        self.title("Gestionar metadata")
        self.geometry("680x470")
        self.minsize(620, 430)
        self.transient(app)

        self.base_url = tk.StringVar(value=app.base_url)
        self.username = tk.StringVar()
        self.password = tk.StringVar()
        self.timeout = tk.StringVar(value="60")
        self.insecure = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="Selecciona un XML local o descarga un metadata nuevo.")
        self.file_info = tk.StringVar(value=app.metadata_summary())
        self._build()

    def _build(self) -> None:
        outer = ttk.Frame(self, padding=16)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)

        ttk.Label(outer, text="Metadata activo", font=("Segoe UI", 11, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(outer, textvariable=self.file_info, wraplength=610).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(4, 16))

        ttk.Button(outer, text="Seleccionar XML local", command=self.select_local).grid(
            row=2, column=0, sticky="w")
        ttk.Button(outer, text="Actualizar info", command=self.refresh_info).grid(
            row=2, column=1, sticky="w", padx=(8, 0))

        ttk.Separator(outer).grid(row=3, column=0, columnspan=3, sticky="ew", pady=16)
        ttk.Label(outer, text="Descargar metadata", font=("Segoe UI", 11, "bold")).grid(
            row=4, column=0, columnspan=3, sticky="w")

        labels = ("URL base", "Usuario", "Contraseña", "Timeout (segundos)")
        values = (self.base_url, self.username, self.password, self.timeout)
        for row, (label, variable) in enumerate(zip(labels, values), start=5):
            ttk.Label(outer, text=label).grid(row=row, column=0, sticky="w", pady=4)
            show = "*" if label == "Contraseña" else ""
            if label == "URL base":
                url_entry, _ = create_placeholder_entry(
                    outer, variable, "{url}/odata/v2", width=58)
                url_entry.grid(row=row, column=1, columnspan=2, sticky="ew", pady=4)
            else:
                ttk.Entry(outer, textvariable=variable, show=show, width=58).grid(
                    row=row, column=1, columnspan=2, sticky="ew", pady=4)

        ttk.Checkbutton(outer, text="Desactivar validación SSL (solo pruebas)",
                        variable=self.insecure).grid(row=9, column=0, columnspan=3, sticky="w", pady=(6, 8))
        ttk.Button(outer, text="Descargar y guardar XML", command=self.download).grid(
            row=10, column=0, sticky="w")
        ttk.Label(outer, textvariable=self.status, wraplength=600).grid(
            row=11, column=0, columnspan=3, sticky="w", pady=(12, 0))

    def refresh_info(self) -> None:
        self.file_info.set(self.app.metadata_summary())

    def select_local(self) -> None:
        path = filedialog.askopenfilename(
            parent=self, title="Seleccionar metadata OData", filetypes=[("XML", "*.xml"), ("Todos", "*.*")])
        if path:
            self.status.set("Cargando metadata local…")
            self.app.load_metadata_file(path, self._after_load)

    def download(self) -> None:
        base_url = self.base_url.get().strip()
        username = self.username.get().strip()
        password = self.password.get()
        if not all((base_url, username, password)):
            messagebox.showwarning(APP_TITLE, "Completa URL base, usuario y contraseña.", parent=self)
            return
        try:
            timeout = int(self.timeout.get())
            if timeout <= 0:
                raise ValueError
        except ValueError:
            messagebox.showwarning(APP_TITLE, "El timeout debe ser un entero positivo.", parent=self)
            return
        destination = filedialog.asksaveasfilename(
            parent=self, title="Guardar metadata", defaultextension=".xml",
            initialfile=f"sf_metadata_{datetime.now():%Y%m%d_%H%M%S}.xml",
            filetypes=[("XML", "*.xml")])
        if not destination:
            return
        self.status.set("Descargando y analizando metadata…")

        def worker() -> None:
            try:
                xml_bytes = download_metadata(base_url, username, password, self.insecure.get(), timeout,
                                              cache_path=destination, refresh_cache=True)
                model = parse_metadata(xml_bytes)
                self.app.after(0, lambda: self._downloaded(destination, model, base_url))
            except Exception as exc:  # Los detalles se comunican sin registrar contraseñas.
                self.app.after(0, lambda: self._failed(str(exc)))

        Thread(target=worker, daemon=True).start()

    def _downloaded(self, path: str, model: MetadataModel, base_url: str) -> None:
        self.password.set("")
        self.app.set_metadata(model, path, base_url)
        self.file_info.set(self.app.metadata_summary())
        self.status.set("Metadata descargado, guardado y cargado correctamente.")

    def _after_load(self, error: Optional[str] = None) -> None:
        if error:
            self._failed(error)
            return
        self.file_info.set(self.app.metadata_summary())
        self.status.set("Metadata local cargado correctamente.")

    def _failed(self, detail: str) -> None:
        self.status.set("No fue posible cargar el metadata.")
        messagebox.showerror(APP_TITLE, detail, parent=self)


class ODataFinderApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1390x820")
        self.minsize(1040, 650)
        self.model: Optional[MetadataModel] = None
        self.metadata_path: Optional[str] = None
        self.base_url = ""
        self.result_payload: dict = {}
        self.tree_item_results: dict[str, dict] = {}
        self._selected_cell: Optional[tuple[str, str]] = None
        self._all_entities: list[str] = []
        self._queue: queue.Queue = queue.Queue()
        self._active_search_cancel: Optional[Event] = None
        self._search_generation = 0

        self.root_entity = tk.StringVar(value="")
        self.search_mode = tk.StringVar(value=SEARCH_MODES[0])
        self.search_term = tk.StringVar()
        self.max_depth = tk.IntVar(value=6)
        self.max_alternative_paths = tk.IntVar(value=3)
        self.max_results = tk.IntVar(value=10)
        self.exact = tk.BooleanVar(value=True)
        self.include_global = tk.BooleanVar(value=True)
        self.metadata_filter = tk.StringVar()
        self.status = tk.StringVar(value="Abre Gestionar metadata para cargar un archivo XML.")
        self.query_text = tk.StringVar(value="")
        self._build()
        self.after(100, self._process_queue)

    def _build(self) -> None:
        style = ttk.Style(self)
        style.configure("Header.TLabel", font=("Segoe UI", 12, "bold"))
        style.configure("Meta.TLabel", foreground="#46505A")
        style.configure("Placeholder.TLabel", foreground="#767676")

        top = ttk.Frame(self, padding=(14, 12, 14, 6))
        top.pack(fill="x")
        ttk.Label(top, text=APP_TITLE, style="Header.TLabel").pack(side="left")
        ttk.Button(top, text="Gestionar metadata", command=self.open_metadata).pack(side="right")

        info = ttk.Frame(self, padding=(14, 0, 14, 8))
        info.pack(fill="x")
        ttk.Label(info, textvariable=self.status, style="Meta.TLabel").pack(side="left")

        search = ttk.LabelFrame(self, text="Búsqueda", padding=12)
        search.pack(fill="x", padx=14, pady=(0, 8))
        search.columnconfigure(1, weight=1)
        search.columnconfigure(3, weight=1)

        ttk.Label(search, text="Entidad raíz").grid(row=0, column=0, sticky="w", padx=(0, 6), pady=4)
        self.entity_combo = ttk.Combobox(search, textvariable=self.root_entity, width=42)
        self.entity_combo.grid(row=0, column=1, sticky="ew", pady=4)
        self.entity_combo.bind("<KeyRelease>", self._filter_entities)
        self.entity_combo.bind("<<ComboboxSelected>>", self._show_root_info)

        ttk.Label(search, text="Modo").grid(row=0, column=2, sticky="w", padx=(14, 6), pady=4)
        ttk.Combobox(search, textvariable=self.search_mode, values=SEARCH_MODES,
                     state="readonly", width=25).grid(row=0, column=3, sticky="ew", pady=4)

        ttk.Label(search, text="Texto, patrón o ruta").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=4)
        search_entry, _ = create_placeholder_entry(
            search, self.search_term,
            "Ej.: custom_*, rmk_region/externalCode o */degreeNav/externalCode")
        search_entry.grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Label(search, text="Profundidad máxima").grid(row=1, column=2, sticky="w", padx=(14, 6), pady=4)
        limits = ttk.Frame(search)
        limits.grid(row=1, column=3, sticky="w", pady=4)
        ttk.Spinbox(limits, from_=0, to=20, textvariable=self.max_depth, width=5).pack(side="left")
        ttk.Label(limits, text="Rutas alternativas máximas").pack(side="left", padx=(10, 5))
        ttk.Spinbox(limits, from_=1, to=100, textvariable=self.max_alternative_paths, width=5).pack(side="left")
        ttk.Label(limits, text="Coincidencias máximas").pack(side="left", padx=(10, 5))
        ttk.Spinbox(limits, from_=1, to=10000, textvariable=self.max_results, width=6).pack(side="left")

        ttk.Label(search, text="Filtro de metadata (opcional)").grid(row=2, column=0, sticky="w", padx=(0, 6), pady=4)
        filter_entry, _ = create_placeholder_entry(
            search, self.metadata_filter,
            "Ej.: tipo:SFOData.PicklistOption AND destino_propiedad:externalCode")
        filter_entry.grid(row=2, column=1, columnspan=3, sticky="ew", pady=4)
        ttk.Checkbutton(search, text="Coincidencia exacta", variable=self.exact).grid(row=3, column=0, sticky="w", pady=(6, 0))
        ttk.Checkbutton(search, text="Incluir coincidencias globales", variable=self.include_global).grid(row=3, column=1, sticky="w", pady=(6, 0))
        ttk.Button(search, text="Buscar", command=self.start_search).grid(row=3, column=3, sticky="e", pady=(6, 0))
        ttk.Label(search,
                  text="Nombre: usa * y ? (ej. custom_*). Ruta: usa /; * como segmento abarca directorios y dentro del nombre filtra (ej. *Nav/externalCode).",
                  style="Meta.TLabel", wraplength=1050).grid(
            row=4, column=0, columnspan=4, sticky="w", pady=(7, 0))

        root_info = ttk.Frame(self, padding=(14, 0, 14, 8))
        root_info.pack(fill="x")
        self.root_summary = tk.StringVar(value="")
        ttk.Label(root_info, textvariable=self.root_summary, style="Meta.TLabel").pack(side="left")

        columns = ("field", "entity", "entity_set", "property", "type", "depth", "search", "queryable", "path")
        results = ttk.Frame(self, padding=(14, 0, 14, 8))
        results.pack(fill="both", expand=True)
        self.tree_columns = columns
        self.tree = ttk.Treeview(results, columns=columns, show="headings", selectmode="extended")
        headings = {"field": "Búsqueda", "entity": "Entidad", "entity_set": "EntitySet", "property": "Propiedad / Nav.",
                    "type": "Tipo", "depth": "Nivel", "search": "Modo", "queryable": "Queryable", "path": "Ruta"}
        widths = {"field": 145, "entity": 180, "entity_set": 170, "property": 170, "type": 175,
                  "depth": 60, "search": 95, "queryable": 85, "path": 510}
        for col in columns:
            self.tree.heading(col, text=headings[col])
            self.tree.column(col, width=widths[col], minwidth=60, stretch=False)
        self.tree.tag_configure("template", foreground="#A45100")
        self.tree.tag_configure("direct", foreground="#0B6E4F")
        yscroll = ttk.Scrollbar(results, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(results, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.bind("<<TreeviewSelect>>", self.update_query_from_selection)
        self.tree.bind("<Button-1>", self.remember_selected_cell, add="+")
        self.tree.bind("<Control-c>", self.copy_selected_cell)
        self.tree.bind("<Button-3>", self.open_result_menu)
        self.tree.bind("<Double-1>", self.open_result_details)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        results.columnconfigure(0, weight=1)
        results.rowconfigure(0, weight=1)

        cell_bar = ttk.Frame(self, padding=(14, 0, 14, 8))
        cell_bar.pack(fill="x")
        self.selected_cell_label = tk.StringVar(value="Celda seleccionada: —")
        self.selected_cell_value = tk.StringVar(value="")
        ttk.Label(cell_bar, textvariable=self.selected_cell_label, style="Meta.TLabel").pack(side="left")
        self.selected_cell_entry = ttk.Entry(cell_bar, textvariable=self.selected_cell_value, state="readonly")
        self.selected_cell_entry.pack(side="left", fill="x", expand=True, padx=(10, 0))
        ttk.Button(cell_bar, text="Copiar celda", command=self.copy_selected_cell).pack(side="left", padx=(8, 0))

        bottom = ttk.LabelFrame(self, text="Consulta OData recomendada", padding=10)
        bottom.pack(fill="x", padx=14, pady=(0, 12))
        ttk.Entry(bottom, textvariable=self.query_text, state="readonly").pack(side="left", fill="x", expand=True)
        ttk.Button(bottom, text="Copiar", command=self.copy_query).pack(side="left", padx=(8, 0))
        ttk.Button(bottom, text="Exportar JSON", command=self.export_json).pack(side="left", padx=(8, 0))
        self.result_menu = tk.Menu(self, tearoff=False)
        self.result_menu.add_command(label="Copiar celda", command=self.copy_selected_cell)
        self.result_menu.add_command(label="Copiar ruta OData", command=self.copy_selected_route)
        self.result_menu.add_command(label="Copiar fila", command=self.copy_selected_row)

    def metadata_summary(self) -> str:
        if not self.model:
            return "No hay metadata cargado."
        file_name = Path(self.metadata_path).name if self.metadata_path else "metadata en memoria"
        return (f"{file_name} · {format_size(self.model.raw_size)} · "
                f"{len(self.model.entities)} entidades · {len(self.model.entity_sets)} EntitySets")

    def open_metadata(self) -> None:
        MetadataWindow(self)

    def set_metadata(self, model: MetadataModel, path: str, base_url: str = "") -> None:
        self.model, self.metadata_path, self.base_url = model, path, base_url
        self._all_entities = sorted({entity.name for entity in model.entities.values()}, key=str.lower)
        self.entity_combo["values"] = self._all_entities
        self.status.set("Metadata cargado: " + self.metadata_summary())
        if find_entity(model, self.root_entity.get()) is None and self._all_entities:
            self.root_entity.set(self._all_entities[0])
        self._show_root_info()

    def load_metadata_file(self, path: str, callback: Optional[Callable[[Optional[str]], None]] = None) -> None:
        self.status.set("Cargando y analizando metadata local…")

        def worker() -> None:
            try:
                with open(path, "rb") as handle:
                    model = parse_metadata(handle.read())
                self._queue.put(("metadata", (model, path, "", callback)))
            except Exception as exc:
                self._queue.put(("metadata_error", (str(exc), callback)))

        Thread(target=worker, daemon=True).start()

    def _filter_entities(self, _event: Optional[tk.Event] = None) -> None:
        text = self.root_entity.get().strip().lower()
        values = self._all_entities if not text else [name for name in self._all_entities if text in name.lower()]
        self.entity_combo["values"] = values[:250]

    def _show_root_info(self, _event: Optional[tk.Event] = None) -> None:
        if not self.model:
            self.root_summary.set("")
            return
        entity = find_entity(self.model, self.root_entity.get())
        if entity:
            self.root_summary.set(
                f"EntityType: {entity.name} · EntitySet: {entity.entity_set or '—'} · "
                f"Properties: {len(entity.properties)} · NavigationProperties: {len(entity.navigations)}")
        else:
            self.root_summary.set("La entidad raíz no existe en el metadata cargado.")

    def start_search(self) -> None:
        if not self.model:
            messagebox.showwarning(APP_TITLE, "Primero carga un metadata XML.", parent=self)
            return
        root = self.root_entity.get().strip()
        mode = self.search_mode.get()
        term = self.search_term.get().strip()
        # Pegar una ruta es una intención inequívoca. Así se evita que una
        # ruta válida se trate por error como un nombre literal de propiedad.
        if mode == "Nombre de propiedad" and "/" in term:
            mode = "Ruta / patrón OData"
            self.search_mode.set(mode)
        if find_entity(self.model, root) is None:
            messagebox.showwarning(APP_TITLE, "Selecciona una entidad raíz válida.", parent=self)
            return
        if mode != "Navegaciones de la raíz" and not term:
            messagebox.showwarning(APP_TITLE, "Ingresa un texto para buscar.", parent=self)
            return
        try:
            depth = int(self.max_depth.get())
            max_alternative_paths = int(self.max_alternative_paths.get())
            max_results = int(self.max_results.get())
            if depth < 0 or max_alternative_paths < 1 or max_results < 1:
                raise ValueError
        except (ValueError, tk.TclError):
            messagebox.showwarning(
                APP_TITLE,
                "La profundidad debe ser mayor o igual a cero y los límites deben ser mayores que cero.",
                parent=self,
            )
            return
        metadata_filter = self.metadata_filter.get().strip()
        if self._active_search_cancel:
            self._active_search_cancel.set()
        self._search_generation += 1
        search_generation = self._search_generation
        cancel_event = Event()
        self._active_search_cancel = cancel_event
        self.status.set("Buscando rutas…")

        def worker() -> None:
            try:
                results = self._search(root, mode, term, depth, max_alternative_paths,
                                       max_results, metadata_filter, cancel_event)
                if cancel_event.is_set():
                    return
                query = build_select_expand(self.model, root, results, None, self.base_url or None)
                if cancel_event.is_set():
                    return
                payload = {"metadata_file": self.metadata_path, "entity": root, "mode": mode,
                           "term": term, "max_depth": depth, "results": results,
                           "max_results": max_results,
                           "recommended_query": query}
                self._queue.put(("search", (search_generation, payload)))
            except Exception as exc:
                if not cancel_event.is_set():
                    self._queue.put(("search_error", (search_generation, str(exc))))

        Thread(target=worker, daemon=True).start()

    def _search(self, root: str, mode: str, term: str, depth: int,
                max_alternative_paths: int, max_results: int, metadata_filter: str,
                cancel_event: Event) -> list[dict]:
        assert self.model is not None
        if mode == "Nombre de propiedad":
            direct = search_direct_properties(self.model, root, [term], self.exact.get())
            if cancel_event.is_set():
                return []
            navigation = recursive_navigation_search(self.model, root, [term], depth, self.exact.get())
            if cancel_event.is_set():
                return []
            global_results = global_property_search(self.model, [term], self.exact.get()) if self.include_global.get() else []
            return self._finalize_results(direct + navigation + global_results, metadata_filter, max_results)
        if mode == "Ruta / patrón OData":
            results = search_odata_path_pattern(self.model, root, term, depth,
                                                max_paths_per_state=max_alternative_paths,
                                                include_global=self.include_global.get(),
                                                cancel_check=cancel_event.is_set)
            return self._finalize_results(results, metadata_filter, max_results)
        if mode == "Tipo OData":
            return self._finalize_results(
                search_properties_by_attribute(self.model, root, term, "type", depth,
                                               self.exact.get(), self.include_global.get()),
                metadata_filter,
                max_results,
            )
        if mode == "sap:picklist":
            return self._finalize_results(
                search_properties_by_attribute(self.model, root, term, "picklist", depth,
                                               self.exact.get(), self.include_global.get()),
                metadata_filter,
                max_results,
            )
        if mode == "Etiqueta sap:label":
            return self._finalize_results(
                search_properties_by_attribute(self.model, root, term, "label", depth,
                                               self.exact.get(), self.include_global.get()),
                metadata_filter,
                max_results,
            )
        if mode == "Entidad relacionada":
            related = search_related_entities(self.model, root, term, depth)
            return self._finalize_results([
                {"field": term, "entity": item["entity"], "entity_set": item["entity_set"],
                 "property_name": item["navigation_property"], "property_type": item["entity"],
                 "navigation_path": item["navigation_path"], "depth": item["depth"],
                 "odata_path": item.get("odata_path", ""),
                 "search_type": "RELATED ENTITY", "queryable": item["queryable"],
                 "match_kind": "navigation_property"} for item in related
            ], metadata_filter, max_results)
        entity = find_entity(self.model, root)
        return self._finalize_results([
            {"field": "NavigationProperty", "entity": entity.name, "entity_set": entity.entity_set,
             "property_name": nav.name, "property_type": nav.target_entity_type or "UNKNOWN",
             "navigation_path": [entity.name, nav.name, (nav.target_entity_type or "UNKNOWN").rsplit(".", 1)[-1]],
             "odata_path": nav.name,
             "depth": 1, "search_type": "NAVIGATION", "queryable": "UNKNOWN",
             "match_kind": "navigation_property"} for nav in entity.navigations
        ], metadata_filter, max_results)

    def _finalize_results(self, results: list[dict], metadata_filter: str,
                          max_results: int) -> list[dict]:
        assert self.model is not None
        return self._limit_results(
            filter_metadata_results(self.model, results, metadata_filter), max_results)

    @staticmethod
    def _limit_results(results: list[dict], max_results: int) -> list[dict]:
        """Prioriza rutas cortas desde la raíz y deja coincidencias globales al final."""
        return sorted(results, key=lambda item: (
            item.get("search_type") == "GLOBAL",
            item.get("depth", 0),
            result_odata_route(item).lower(),
        ))[:max_results]

    def _process_queue(self) -> None:
        try:
            while True:
                event, data = self._queue.get_nowait()
                if event == "metadata":
                    model, path, base_url, callback = data
                    self.set_metadata(model, path, base_url)
                    if callback:
                        callback(None)
                elif event == "metadata_error":
                    error, callback = data
                    self.status.set("No se pudo cargar el metadata.")
                    if callback:
                        callback(error)
                    else:
                        messagebox.showerror(APP_TITLE, error, parent=self)
                elif event == "search":
                    generation, payload = data
                    if generation == self._search_generation:
                        self.show_results(payload)
                elif event == "search_error":
                    generation, error = data
                    if generation == self._search_generation:
                        self.status.set("La búsqueda no pudo completarse.")
                        messagebox.showerror(APP_TITLE, error, parent=self)
        except queue.Empty:
            pass
        self.after(100, self._process_queue)

    def show_results(self, payload: dict) -> None:
        self.result_payload = payload
        self.tree_item_results.clear()
        self._selected_cell = None
        self.selected_cell_label.set("Celda seleccionada: —")
        self.selected_cell_value.set("")
        for item in self.tree.get_children():
            self.tree.delete(item)
        route_values = [result_odata_route(item) for item in payload["results"]]
        # El ancho de una celda no aumenta automáticamente con su texto en un
        # Treeview. Ajustarlo a la ruta más larga permite recorrerla completa
        # mediante la barra horizontal, en vez de cortarla dentro de la celda.
        route_font = tkfont.nametofont("TkDefaultFont")
        route_width = max(510, *(route_font.measure(route) + 32 for route in route_values))
        self.tree.column("path", width=route_width, minwidth=510, stretch=False)
        self.tree.xview_moveto(0)
        for item, route_value in zip(payload["results"], route_values):
            tags: list[str] = []
            if item.get("queryable") == "NO":
                tags.append("template")
            elif item.get("search_type") == "DIRECT":
                tags.append("direct")
            tree_id = self.tree.insert("", "end", values=(
                item.get("field", ""), item.get("entity", ""), item.get("entity_set") or "",
                item.get("property_name", ""), item.get("property_type", ""), item.get("depth", ""),
                item.get("search_type", ""), item.get("queryable", ""),
                route_value), tags=tags)
            self.tree_item_results[tree_id] = item
        self.query_text.set("")
        if not payload["results"] and payload.get("mode") == "Ruta / patrón OData":
            self.status.set(
                "No hubo coincidencias. Verifica la entidad raíz y la profundidad; "
                "para la ruta indicada usa JobRequisitionPosting y al menos 5 niveles."
            )
            return
        self.status.set(
            f"Búsqueda terminada: {len(payload['results'])} coincidencia(s). "
            f"Límite configurado: {payload.get('max_results', '—')}. "
            "Selecciona una o más filas para generar la consulta."
        )

    def selected_results(self) -> list[dict]:
        return [self.tree_item_results[item_id] for item_id in self.tree.selection()
                if item_id in self.tree_item_results]

    def update_query_from_selection(self, _event: Optional[tk.Event] = None) -> None:
        if not self.model or not self.result_payload:
            return
        selected = self.selected_results()
        root = self.result_payload.get("entity", "")
        usable = [item for item in selected if item.get("search_type") != "GLOBAL"
                  and (item.get("navigation_path") or [""])[0].lower() == root.lower()]
        if not usable:
            self.query_text.set("")
            if selected:
                self.status.set("Las coincidencias globales no pueden generar una consulta desde la entidad raíz seleccionada.")
            return
        query = build_select_expand(self.model, root, usable, None, self.base_url or None)
        self.query_text.set(query.get("relative_url", ""))
        self.status.set(f"Consulta generada para {len(usable)} fila(s) seleccionada(s).")

    def remember_selected_cell(self, event: tk.Event) -> None:
        row_id = self.tree.identify_row(event.y)
        column_id = self.tree.identify_column(event.x)
        if row_id and column_id and column_id != "#0":
            index = int(column_id[1:]) - 1
            if 0 <= index < len(self.tree_columns):
                column = self.tree_columns[index]
                self._selected_cell = (row_id, column)
                self.tree.selection_set(row_id)
                value = self.tree.set(row_id, column)
                self.selected_cell_label.set(f"Celda seleccionada: {column}")
                self.selected_cell_value.set(value)

    def open_result_menu(self, event: tk.Event) -> None:
        self.remember_selected_cell(event)
        row_id = self.tree.identify_row(event.y)
        if row_id:
            self.tree.selection_set(row_id)
            self.result_menu.tk_popup(event.x_root, event.y_root)

    def copy_selected_cell(self, _event: Optional[tk.Event] = None) -> str:
        if not self._selected_cell:
            self.status.set("Selecciona una celda de resultados para copiarla.")
            return "break"
        row_id, column = self._selected_cell
        value = self.tree.set(row_id, column)
        self.clipboard_clear()
        self.clipboard_append(value)
        self.status.set("Celda copiada al portapapeles.")
        return "break"

    def open_result_details(self, event: tk.Event) -> str:
        self.remember_selected_cell(event)
        row_id = self.tree.identify_row(event.y)
        if not row_id or row_id not in self.tree_item_results or not self.model:
            return "break"
        item = self.tree_item_results[row_id]
        entity = find_entity(self.model, item.get("entity", ""))
        if not entity:
            return "break"

        # When the selected result is a NavigationProperty, the useful details
        # are normally on its target EntityType.  For example, degreeNav leads
        # to PicklistOption, where externalCode is defined.
        selected_navigation = next(
            (navigation for navigation in entity.navigations
             if navigation.name.lower() == item.get("property_name", "").lower()),
            None,
        )
        target_entity = (
            find_entity(self.model, selected_navigation.target_entity_type)
            if selected_navigation and selected_navigation.target_entity_type
            else None
        )

        window = tk.Toplevel(self)
        window.title(f"Detalles internos · {entity.name}")
        window.geometry("980x650")
        window.minsize(760, 500)
        window.transient(self)

        header = ttk.Frame(window, padding=14)
        header.pack(fill="x")
        details = (
            ("Campo buscado", item.get("field", "")),
            ("Entidad", entity.name),
            ("EntitySet", entity.entity_set or "—"),
            ("Resultado", item.get("property_name", "")),
            ("Tipo", item.get("property_type", "")),
            ("Ruta OData", result_odata_route(item)),
            ("Queryable", item.get("queryable", "UNKNOWN")),
        )
        if selected_navigation:
            details += (
                ("NavigationProperty", selected_navigation.name),
                ("Entidad destino", selected_navigation.target_entity_type or "UNKNOWN"),
            )
        for row, (label, value) in enumerate(details):
            ttk.Label(header, text=label + ":", font=("Segoe UI", 9, "bold")).grid(
                row=row, column=0, sticky="nw", padx=(0, 8), pady=2)
            ttk.Label(header, text=value, wraplength=760).grid(row=row, column=1, sticky="nw", pady=2)
        ttk.Button(header, text="Copiar ruta", command=lambda: self._copy_text(result_odata_route(item))).grid(
            row=0, column=2, rowspan=2, sticky="ne", padx=(18, 0))

        filter_var = tk.StringVar()
        filter_row = len(details)
        ttk.Label(header, text="Buscar en detalles:", font=("Segoe UI", 9, "bold")).grid(
            row=filter_row, column=0, sticky="w", padx=(0, 8), pady=(9, 0))
        filter_entry = ttk.Entry(header, textvariable=filter_var, width=58)
        filter_entry.grid(row=filter_row, column=1, sticky="ew", pady=(9, 0))
        header.columnconfigure(1, weight=1)

        detail_status = tk.StringVar(
            value="Selecciona una celda; usa Ctrl+C o clic derecho para copiarla."
        )
        ttk.Label(window, textvariable=detail_status, anchor="w", padding=(14, 0, 14, 10)).pack(
            side="bottom", fill="x")

        notebook = ttk.Notebook(window)
        notebook.pack(fill="both", expand=True, padx=14, pady=(0, 8))

        refreshers: list[Callable[[], None]] = []

        def enable_detail_cell_copy(tree: ttk.Treeview, columns: tuple[str, ...]) -> None:
            """Provide cell-level clipboard actions for a details table."""
            selected_cell: list[Optional[tuple[str, str]]] = [None]

            def remember_cell(selected_event: tk.Event) -> None:
                row_id = tree.identify_row(selected_event.y)
                column_id = tree.identify_column(selected_event.x)
                if not row_id or not column_id or column_id == "#0":
                    return
                index = int(column_id[1:]) - 1
                if not 0 <= index < len(columns):
                    return
                column = columns[index]
                selected_cell[0] = (row_id, column)
                tree.selection_set(row_id)
                detail_status.set(
                    f"Celda seleccionada: {column}. Usa Ctrl+C o clic derecho para copiarla."
                )

            def copy_cell(_event: Optional[tk.Event] = None) -> str:
                if not selected_cell[0]:
                    detail_status.set("Selecciona una celda para copiarla.")
                    return "break"
                row_id, column = selected_cell[0]
                self.clipboard_clear()
                self.clipboard_append(tree.set(row_id, column))
                detail_status.set(f"Celda '{column}' copiada al portapapeles.")
                return "break"

            def copy_row() -> None:
                selected_rows = tree.selection()
                if not selected_rows:
                    detail_status.set("Selecciona una fila para copiarla.")
                    return
                self.clipboard_clear()
                self.clipboard_append("\n".join(
                    "\t".join(tree.item(row_id, "values")) for row_id in selected_rows
                ))
                detail_status.set("Fila(s) copiada(s) al portapapeles.")

            menu = tk.Menu(tree, tearoff=False)
            menu.add_command(label="Copiar celda", command=copy_cell)
            menu.add_command(label="Copiar fila", command=copy_row)

            def open_menu(menu_event: tk.Event) -> None:
                remember_cell(menu_event)
                if tree.identify_row(menu_event.y):
                    try:
                        menu.tk_popup(menu_event.x_root, menu_event.y_root)
                    finally:
                        menu.grab_release()

            tree.bind("<Button-1>", remember_cell, add="+")
            tree.bind("<Control-c>", copy_cell, add="+")
            tree.bind("<Button-3>", open_menu)

        def add_entity_tabs(subject: object, prefix: str) -> ttk.Frame:
            """Add filterable properties and navigations tabs for one EntityType."""
            # EntityInfo is intentionally duck-typed here to keep the GUI
            # independent from the implementation details of the core module.
            properties = getattr(subject, "properties")
            navigations = getattr(subject, "navigations")
            subject_name = getattr(subject, "name")

            properties_tab = ttk.Frame(notebook, padding=8)
            navigations_tab = ttk.Frame(notebook, padding=8)
            if prefix == "Destino":
                properties_title = f"Destino propiedades ({len(properties)})"
                navigations_title = f"Destino navegaciones ({len(navigations)})"
            else:
                properties_title = f"Origen propiedades · {subject_name} ({len(properties)})"
                navigations_title = f"Origen navegaciones · {subject_name} ({len(navigations)})"
            notebook.add(properties_tab, text=properties_title)
            notebook.add(navigations_tab, text=navigations_title)

            prop_columns = ("name", "type", "nullable", "label", "picklist")
            prop_tree = ttk.Treeview(properties_tab, columns=prop_columns, show="headings")
            prop_headers = {
                "name": "Nombre", "type": "Tipo", "nullable": "Nullable",
                "label": "sap:label", "picklist": "sap:picklist",
            }
            prop_widths = {"name": 220, "type": 190, "nullable": 80, "label": 270, "picklist": 190}
            for column in prop_columns:
                prop_tree.heading(column, text=prop_headers[column])
                prop_tree.column(column, width=prop_widths[column], minwidth=70, stretch=column == "label")
            self._pack_tree(properties_tab, prop_tree)
            enable_detail_cell_copy(prop_tree, prop_columns)

            nav_columns = ("name", "target", "entity_set", "relationship")
            nav_tree = ttk.Treeview(navigations_tab, columns=nav_columns, show="headings")
            nav_headers = {
                "name": "NavigationProperty", "target": "Entidad destino",
                "entity_set": "EntitySet destino", "relationship": "Association",
            }
            nav_widths = {"name": 230, "target": 250, "entity_set": 200, "relationship": 300}
            for column in nav_columns:
                nav_tree.heading(column, text=nav_headers[column])
                nav_tree.column(column, width=nav_widths[column], minwidth=90, stretch=column == "relationship")
            self._pack_tree(navigations_tab, nav_tree)
            enable_detail_cell_copy(nav_tree, nav_columns)

            def refresh() -> None:
                needle = filter_var.get().strip().lower()
                prop_tree.delete(*prop_tree.get_children())
                nav_tree.delete(*nav_tree.get_children())
                for prop in properties:
                    values = (prop.name, prop.type, prop.nullable or "", prop.label, prop.picklist)
                    if not needle or needle in " ".join(str(value) for value in values).lower():
                        prop_tree.insert("", "end", values=values)
                for navigation in navigations:
                    values = (
                        navigation.name, navigation.target_entity_type or "UNKNOWN",
                        navigation.target_entity_set or "", navigation.relationship,
                    )
                    if not needle or needle in " ".join(str(value) for value in values).lower():
                        nav_tree.insert("", "end", values=values)

            refreshers.append(refresh)
            refresh()
            return properties_tab

        target_properties_tab: Optional[ttk.Frame] = None
        if target_entity and target_entity.name != entity.name:
            target_properties_tab = add_entity_tabs(target_entity, "Destino")
        add_entity_tabs(entity, "Origen")

        def refresh_details(*_args: object) -> None:
            for refresh in refreshers:
                refresh()

        filter_var.trace_add("write", refresh_details)
        if target_properties_tab:
            notebook.select(target_properties_tab)
        filter_entry.focus_set()
        return "break"

    @staticmethod
    def _pack_tree(parent: ttk.Frame, tree: ttk.Treeview) -> None:
        yscroll = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        xscroll = ttk.Scrollbar(parent, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

    def _copy_text(self, value: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(value)
        self.status.set("Texto copiado al portapapeles.")

    def copy_selected_route(self) -> None:
        selected = self.selected_results()
        if not selected:
            self.status.set("Selecciona una fila para copiar su ruta.")
            return
        self.clipboard_clear()
        self.clipboard_append("\n".join(result_odata_route(item) for item in selected))
        self.status.set("Ruta(s) OData copiada(s) al portapapeles.")

    def copy_selected_row(self) -> None:
        selected = self.tree.selection()
        if not selected:
            self.status.set("Selecciona una fila para copiarla.")
            return
        lines = ["\t".join(self.tree.item(row_id, "values")) for row_id in selected]
        self.clipboard_clear()
        self.clipboard_append("\n".join(lines))
        self.status.set("Fila(s) copiada(s) al portapapeles.")

    def copy_query(self) -> None:
        if self.query_text.get():
            self.clipboard_clear()
            self.clipboard_append(self.query_text.get())
            self.status.set("Consulta OData copiada al portapapeles.")

    def export_json(self) -> None:
        if not self.result_payload:
            messagebox.showinfo(APP_TITLE, "No hay resultados para exportar.", parent=self)
            return
        path = filedialog.asksaveasfilename(parent=self, title="Exportar resultados", defaultextension=".json",
                                            initialfile="sf_odata_resultados.json", filetypes=[("JSON", "*.json")])
        if not path:
            return
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(self.result_payload, handle, indent=2, ensure_ascii=False)
        self.status.set("Resultados exportados a JSON.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", help="XML OData local para cargar al iniciar")
    args = parser.parse_args()
    app = ODataFinderApp()
    if args.metadata:
        app.load_metadata_file(os.path.abspath(args.metadata))
    app.mainloop()


if __name__ == "__main__":
    main()

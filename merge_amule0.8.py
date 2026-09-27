#!/usr/bin/env python3

import argparse
import os
import struct
import sys
import tempfile
import time


BUFFER = 1024 * 1024


# ============================================================
# Utilitaires
# ============================================================

def fmt(n):
    return f"{n:,}".replace(",", " ")


def normalize_gaps(gaps):
    if not gaps:
        return []

    gaps = sorted(gaps)
    result = [gaps[0]]

    for start, end in gaps[1:]:
        old_start, old_end = result[-1]

        if start <= old_end:
            result[-1] = (
                old_start,
                max(old_end, end)
            )
        else:
            result.append((start, end))

    return result


def is_missing(position, gaps):
    for start, end in gaps:
        if position < start:
            return False

        if start <= position < end:
            return True

    return False


def make_boundaries(size, all_gaps):
    points = {0, size}

    for gaps in all_gaps:
        for start, end in gaps:
            points.add(start)
            points.add(end)

    return sorted(points)


def intersection_of_gaps(size, all_gaps):
    """
    Une zone est absente du résultat uniquement si elle est
    absente de TOUS les fichiers.
    """

    boundaries = make_boundaries(size, all_gaps)
    result = []

    for start, end in zip(
        boundaries,
        boundaries[1:]
    ):
        if all(
            is_missing(start, gaps)
            for gaps in all_gaps
        ):
            result.append((start, end))

    return normalize_gaps(result)


# ============================================================
# Lecture des .part.met
# ============================================================

class MetFile:

    def __init__(self, path):
        self.path = path

        with open(path, "rb") as f:
            self.data = f.read()

        self.pos = 0

        self.version = self.read_u8()
        self.date = self.read_u32()
        self.file_hash = self.read_bytes(16)

        self.hash_count = self.read_u16()

        self.chunk_hashes = [
            self.read_bytes(16)
            for _ in range(self.hash_count)
        ]

        self.tag_count = self.read_u32()

        self.tags = [
            self.read_tag()
            for _ in range(self.tag_count)
        ]

        if self.pos != len(self.data):
            raise ValueError(
                f"{path}: données inattendues à la fin du fichier"
            )

    def read_bytes(self, n):
        if self.pos + n > len(self.data):
            raise ValueError(
                f"{self.path}: fichier .met tronqué"
            )

        value = self.data[
            self.pos:self.pos + n
        ]

        self.pos += n
        return value

    def read_u8(self):
        return self.read_bytes(1)[0]

    def read_u16(self):
        return struct.unpack(
            "<H",
            self.read_bytes(2)
        )[0]

    def read_u32(self):
        return struct.unpack(
            "<I",
            self.read_bytes(4)
        )[0]

    def read_tag(self):
        tag_type = self.read_u8()

        name_len = self.read_u16()
        name = self.read_bytes(name_len)

        if tag_type == 0x01:
            value = self.read_bytes(16)

        elif tag_type == 0x02:
            n = self.read_u16()
            value = (
                struct.pack("<H", n)
                + self.read_bytes(n)
            )

        elif tag_type == 0x03:
            value = self.read_bytes(4)

        elif tag_type == 0x04:
            value = self.read_bytes(4)

        elif tag_type == 0x05:
            value = self.read_bytes(1)

        elif tag_type == 0x06:
            n = self.read_u16()
            value = (
                struct.pack("<H", n)
                + self.read_bytes((n // 8) + 1)
            )

        elif tag_type == 0x07:
            n = self.read_u32()
            value = (
                struct.pack("<I", n)
                + self.read_bytes(n)
            )

        elif tag_type == 0x08:
            value = self.read_bytes(2)

        elif tag_type == 0x09:
            value = self.read_bytes(1)

        elif tag_type == 0x0A:
            n = self.read_u16()
            value = (
                struct.pack("<H", n)
                + self.read_bytes(n)
            )

        elif tag_type == 0x0B:
            value = self.read_bytes(8)

        elif 0x11 <= tag_type <= 0x20:
            value = self.read_bytes(tag_type - 0x10)

        else:
            raise ValueError(
                f"{self.path}: type de tag inconnu "
                f"0x{tag_type:02x}"
            )

        return {
            "type": tag_type,
            "name": name,
            "value": value
        }

    def get_uint32_tag(self, name):
        for tag in self.tags:
            if (
                tag["type"] == 0x03
                and tag["name"] == name
            ):
                return struct.unpack(
                    "<I",
                    tag["value"]
                )[0]

        return None

    def get_file_size(self):
        value = self.get_uint32_tag(b"\x02")

        if value is None:
            raise ValueError(
                f"{self.path}: taille du fichier introuvable"
            )

        return value

    def get_gaps(self):
        starts = {}
        ends = {}

        for tag in self.tags:

            if tag["type"] != 0x03:
                continue

            name = tag["name"]

            if len(name) != 2:
                continue

            if name[0] == 0x09:
                starts[name[1]] = struct.unpack(
                    "<I",
                    tag["value"]
                )[0]

            elif name[0] == 0x0A:
                ends[name[1]] = struct.unpack(
                    "<I",
                    tag["value"]
                )[0]

        gaps = []

        for ident in sorted(set(starts) | set(ends)):

            if ident not in starts or ident not in ends:
                raise ValueError(
                    f"{self.path}: gap incomplet "
                    f"(identifiant 0x{ident:02x})"
                )

            start = starts[ident]
            end = ends[ident]

            if end < start:
                raise ValueError(
                    f"{self.path}: gap invalide "
                    f"{start} -> {end}"
                )

            gaps.append((start, end))

        return normalize_gaps(gaps)

    def rebuild(self, gaps, downloaded):
        """
        Reconstitue le .part.met en conservant les métadonnées
        du premier fichier et en remplaçant ses gaps.
        """

        new_tags = []

        for tag in self.tags:

            name = tag["name"]

            # Supprimer les anciens tags de gaps.
            if (
                tag["type"] == 0x03
                and len(name) == 2
                and name[0] in (0x09, 0x0A)
            ):
                continue

            new_tags.append(tag.copy())

        # Mettre à jour le nombre d'octets disponibles.
        updated = False

        for tag in new_tags:

            if (
                tag["type"] == 0x03
                and tag["name"] == b"\x08"
            ):
                tag["value"] = struct.pack(
                    "<I",
                    downloaded
                )
                updated = True

        if not updated:
            raise ValueError(
                f"{self.path}: tag 0x08 introuvable"
            )

        # Ajouter les nouveaux gaps.
        for i, (start, end) in enumerate(gaps):

            ident = 0x30 + i

            new_tags.append({
                "type": 0x03,
                "name": bytes([0x09, ident]),
                "value": struct.pack("<I", start)
            })

            new_tags.append({
                "type": 0x03,
                "name": bytes([0x0A, ident]),
                "value": struct.pack("<I", end)
            })

        output = bytearray()

        output += bytes([self.version])

        output += struct.pack(
            "<I",
            int(time.time())
        )

        output += self.file_hash

        output += struct.pack(
            "<H",
            len(self.chunk_hashes)
        )

        for h in self.chunk_hashes:
            output += h

        output += struct.pack(
            "<I",
            len(new_tags)
        )

        for tag in new_tags:

            output += bytes([tag["type"]])

            output += struct.pack(
                "<H",
                len(tag["name"])
            )

            output += tag["name"]
            output += tag["value"]

        return bytes(output)


# ============================================================
# Vérification des métadonnées
# ============================================================

def validate_mets(mets):

    first = mets[0]

    size = first.get_file_size()

    for i, met in enumerate(mets[1:], start=2):

        if met.version != first.version:
            raise ValueError(
                f"Fichier {i}: version .met différente."
            )

        if met.file_hash != first.file_hash:
            raise ValueError(
                f"Fichier {i}: hash ED2K différent.\n"
                f"Les fichiers ne correspondent pas "
                f"au même téléchargement."
            )

        if met.get_file_size() != size:
            raise ValueError(
                f"Fichier {i}: taille différente."
            )

        if len(met.chunk_hashes) != len(
            first.chunk_hashes
        ):
            raise ValueError(
                f"Fichier {i}: nombre de chunks différent."
            )

        for chunk, (a, b) in enumerate(
            zip(
                first.chunk_hashes,
                met.chunk_hashes
            )
        ):
            if a != b:
                raise ValueError(
                    f"Fichier {i}: hash du chunk "
                    f"{chunk} différent."
                )

    return size


# ============================================================
# Accès aux données
# ============================================================

def compare_range(files, indexes, start, end):
    """
    Vérifie que les données présentes dans tous les fichiers
    indiqués sont strictement identiques sur [start, end).
    """

    if len(indexes) <= 1:
        return True

    reference_index = indexes[0]
    reference_file = files[reference_index]

    for offset in range(start, end, BUFFER):

        block_end = min(
            offset + BUFFER,
            end
        )

        reference_file.seek(offset)

        reference = reference_file.read(
            block_end - offset
        )

        if len(reference) != block_end - offset:
            raise IOError(
                f"Lecture incomplète dans "
                f"{os.path.basename(reference_file.name)} "
                f"à {offset}"
            )

        for index in indexes[1:]:

            f = files[index]
            f.seek(offset)

            data = f.read(
                block_end - offset
            )

            if len(data) != block_end - offset:
                raise IOError(
                    f"Lecture incomplète dans "
                    f"{os.path.basename(f.name)} "
                    f"à {offset}"
                )

            if data != reference:
                return False

    return True


def copy_range(src, dst, start, end):

    src.seek(start)
    dst.seek(start)

    remaining = end - start

    while remaining:

        n = min(BUFFER, remaining)

        data = src.read(n)

        if len(data) != n:
            raise IOError(
                f"Lecture incomplète à {start}"
            )

        dst.write(data)

        remaining -= n


# ============================================================
# Fusion
# ============================================================

def merge(parts, args):

    # --------------------------------------------------------
    # Trouver automatiquement les .part.met
    # --------------------------------------------------------

    met_paths = []

    for part in parts:

        if not part.endswith(".part"):
            raise ValueError(
                f"{part}: ce fichier ne se termine pas par .part"
            )

        met = part + ".met"

        if not os.path.exists(met):
            raise FileNotFoundError(
                f"{part}: fichier associé introuvable : {met}"
            )

        met_paths.append(met)

    mets = [
        MetFile(path)
        for path in met_paths
    ]

    # --------------------------------------------------------
    # Validation
    # --------------------------------------------------------

    size = validate_mets(mets)

    print()
    print("=" * 72)
    print("ANALYSE aMule")
    print("=" * 72)

    print(
        f"Fichiers sources : {len(parts)}"
    )

    print(
        f"Hash ED2K       : {mets[0].file_hash.hex()}"
    )

    print(
        f"Taille          : {fmt(size)} octets"
    )

    print(
        f"Chunks          : {len(mets[0].chunk_hashes)}"
    )

    # --------------------------------------------------------
    # Gaps
    # --------------------------------------------------------

    all_gaps = []

    for part, met in zip(parts, mets):

        actual_size = os.path.getsize(part)

        if actual_size != size:
            raise ValueError(
                f"{part}: taille réelle {actual_size} "
                f"au lieu de {size}"
            )

        gaps = met.get_gaps()
        all_gaps.append(gaps)

        print()
        print(f"Gaps de {part} :")

        if not gaps:
            print("  aucun")
        else:
            for start, end in gaps:
                print(
                    f"  {fmt(start)} -> {fmt(end)} "
                    f"({fmt(end - start)} octets)"
                )

    # --------------------------------------------------------
    # Sortie
    # --------------------------------------------------------

    if args.output:

        output_base = args.output

    else:

        first = os.path.basename(parts[0])

        if first.endswith(".part"):
            first = first[:-5]

        output_base = os.path.join(
            os.path.dirname(parts[0]),
            first + "-merged"
        )

    output_part = output_base + ".part"
    output_met = output_base + ".part.met"

    if not args.dry_run:

        if os.path.exists(output_part) and not args.force:
            raise FileExistsError(
                f"{output_part} existe déjà. "
                f"Utilise --force pour le remplacer."
            )

        if os.path.exists(output_met) and not args.force:
            raise FileExistsError(
                f"{output_met} existe déjà. "
                f"Utilise --force pour le remplacer."
            )

    # --------------------------------------------------------
    # Fusion
    # --------------------------------------------------------

    boundaries = make_boundaries(
        size,
        all_gaps
    )

    only = [0] * len(parts)

    common = 0
    missing = 0

    temp_dir = os.path.dirname(
        os.path.abspath(output_part)
    )

    temp_part = None

    if not args.dry_run:

        fd, temp_part = tempfile.mkstemp(
            prefix=".amule-merge-",
            suffix=".part.tmp",
            dir=temp_dir
        )

        os.close(fd)

    files = []

    try:

        if not args.dry_run:

            files = [
                open(part, "rb")
                for part in parts
            ]

            output = open(
                temp_part,
                "wb"
            )

        else:

            files = [
                open(part, "rb")
                for part in parts
            ]

            output = None

        for start, end in zip(
            boundaries,
            boundaries[1:]
        ):

            length = end - start

            present = []

            for i, gaps in enumerate(all_gaps):

                if not is_missing(start, gaps):
                    present.append(i)

            # ------------------------------------------------
            # Absente partout
            # ------------------------------------------------

            if not present:

                print(
                    f"MANQUANT : {fmt(start)} -> "
                    f"{fmt(end)} ({fmt(length)})"
                )

                missing += length

                continue

            # ------------------------------------------------
            # Présent dans plusieurs fichiers
            # ------------------------------------------------

            if len(present) > 1:

                if not compare_range(
                    files,
                    present,
                    start,
                    end
                ):
                    raise RuntimeError(
                        "\nCONFLIT : les fichiers contiennent "
                        "des données différentes dans la zone "
                        f"{fmt(start)} -> {fmt(end)}.\n"
                        "La fusion est annulée."
                    )

                print(
                    f"COMMUN   : {fmt(start)} -> "
                    f"{fmt(end)} ({fmt(length)})"
                )

                common += length

                if output:
                    copy_range(
                        files[present[0]],
                        output,
                        start,
                        end
                    )

                continue

            # ------------------------------------------------
            # Présent dans un seul fichier
            # ------------------------------------------------

            index = present[0]

            print(
                f"DEPUIS {chr(65 + index)} : "
                f"{fmt(start)} -> {fmt(end)} "
                f"({fmt(length)})"
            )

            only[index] += length

            if output:
                copy_range(
                    files[index],
                    output,
                    start,
                    end
                )

        # ----------------------------------------------------
        # Fin de la fusion
        # ----------------------------------------------------

        downloaded = size - missing

        print()
        print("=" * 72)
        print("RÉSULTAT")
        print("=" * 72)

        for i, amount in enumerate(only):

            print(
                f"Uniquement {chr(65 + i)}"
                f" ({os.path.basename(parts[i])})"
                f" : {fmt(amount)} octets"
            )

        print(
            f"Communs identiques  : "
            f"{fmt(common)} octets"
        )

        print(
            f"Absents de tous     : "
            f"{fmt(missing)} octets"
        )

        print(
            f"Données disponibles : "
            f"{fmt(downloaded)} / {fmt(size)}"
        )

        print(
            f"Progression         : "
            f"{100.0 * downloaded / size:.2f} %"
        )

        merged_gaps = intersection_of_gaps(
            size,
            all_gaps
        )

        print()
        print("Gaps du fichier fusionné :")

        if not merged_gaps:
            print("  aucun")
        else:
            for start, end in merged_gaps:
                print(
                    f"  {fmt(start)} -> {fmt(end)} "
                    f"({fmt(end - start)} octets)"
                )

        # ----------------------------------------------------
        # Dry-run
        # ----------------------------------------------------

        if args.dry_run:

            print()
            print(
                "DRY-RUN : aucun fichier résultat "
                "n'a été créé."
            )

            return

        # ----------------------------------------------------
        # Finalisation .part
        # ----------------------------------------------------

        output.truncate(size)
        output.close()
        output = None

        # ----------------------------------------------------
        # Construction du .part.met
        # ----------------------------------------------------

        met_data = mets[0].rebuild(
            merged_gaps,
            downloaded
        )

        fd, temp_met = tempfile.mkstemp(
            prefix=".amule-merge-",
            suffix=".met.tmp",
            dir=temp_dir
        )

        os.close(fd)

        try:

            with open(temp_met, "wb") as f:
                f.write(met_data)

            os.replace(
                temp_part,
                output_part
            )

            os.replace(
                temp_met,
                output_met
            )

        finally:

            if os.path.exists(temp_met):
                os.unlink(temp_met)

        temp_part = None

        print()
        print("Fusion terminée.")
        print()
        print(f"PART : {output_part}")
        print(f"MET  : {output_met}")

    finally:

        for f in files:

            try:
                f.close()
            except Exception:
                pass

        if output is not None:

            try:
                output.close()
            except Exception:
                pass

        if temp_part and os.path.exists(temp_part):
            os.unlink(temp_part)


# ============================================================
# Interface
# ============================================================

def main():

    parser = argparse.ArgumentParser(
        description=(
            "Fusionne deux ou plusieurs fichiers .part "
            "aMule correspondant au même téléchargement."
        )
    )

    parser.add_argument(
        "parts",
        nargs="+",
        help=(
            "Deux fichiers .part ou plus. "
            "Les .part.met associés sont détectés automatiquement."
        )
    )

    parser.add_argument(
        "-o",
        "--output",
        help=(
            "Nom de base de sortie. "
            "Par défaut : <premier-fichier>-merged"
        )
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Analyse et vérifie sans créer de fichier"
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="Remplace les fichiers de sortie existants"
    )

    args = parser.parse_args()

    if len(args.parts) < 2:
        parser.error(
            "Il faut au moins deux fichiers .part."
        )

    try:

        merge(
            args.parts,
            args
        )

    except KeyboardInterrupt:

        print()
        print("Interrompu.")

        sys.exit(130)

    except Exception as e:

        print()
        print("ERREUR :", e)

        sys.exit(1)


if __name__ == "__main__":
    main()

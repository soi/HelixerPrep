from ..core.structure import GenericData, add_paired_dictionaries
from dustdas import fastahelper
import itertools
import copy
import hashlib
from ..core.partitions import CoordinateGenerator
from ..core import helpers


class StructuredGenome(GenericData):
    """Handles meta info for genome and included sequences"""
    def __init__(self):
        super().__init__()
        # todo, make GenericData more specific below
        self.spec += [("sequences", True, StructuredSequence, list),
                      ("meta_info", True, MetaInfoGenome, None)]
        self.meta_info = MetaInfoGenome()
        self.sequences = []

    def add_fasta(self, fasta, smallest_mer=2, largest_mer=2):
        self.meta_info.maybe_add_info_from_fasta(fasta)
        fh = fastahelper.FastaParser()
        for infos, seq in fh.read_fasta(fasta):
            seq_holder = StructuredSequence()
            seq_holder.add_sequence(fasta_header=infos, sequence=seq, smallest_mer=smallest_mer,
                                    largest_mer=largest_mer)
            self.meta_info.add_sequence_meta_info(seq_holder.meta_info)
            self.sequences.append(seq_holder)

    def divvy_each_sequence(self, user_seed, max_len=2000000):
        for seq in self.sequences:
            seq.divvy_up_sequence(user_seed, max_len=max_len)


class StructuredSequence(GenericData):
    def __init__(self):
        super().__init__()
        self.spec += [('slices', True, SequenceSlice, list),
                      ('meta_info', True, MetaInfoSequence, None)]
        self.meta_info = MetaInfoSequence()
        self.slices = []

    def add_sequence(self, fasta_header, sequence, smallest_mer=2, largest_mer=2):
        self.meta_info = MetaInfoSequence()
        self.meta_info.add_sequence(fasta_header=fasta_header, sequence=sequence, smallest_mer=smallest_mer,
                                    largest_mer=largest_mer)
        unsliced = SequenceSlice()
        unsliced.add_sequence(sequence, self.meta_info.seqid)
        self.slices.append(unsliced)

    def full_sequence(self):
        subseqs = []
        for subseq in self.slices:
            subseqs += subseq.sequence
        return ''.join(subseqs)

    def seq_hash(self):
        md5 = hashlib.md5(self.full_sequence().encode('utf-8'))
        return md5.hexdigest()

    def divvy_up_coords(self, user_seed, max_len=2000000):
        seed = self.seq_hash() + user_seed
        cg = CoordinateGenerator(seed, max_len=max_len)
        for coords in cg.divvy_coordinates(seed, self.meta_info.total_bp):
            yield coords

    def divvy_up_sequence(self, user_seed, max_len=2000000):
        sequence = self.full_sequence()
        coords = self.divvy_up_coords(user_seed, max_len=max_len)
        idmaker = helpers.IDMaker(prefix=self.meta_info.seqid + '_')
        slices = []
        for begin, end, pset in coords:
            s = SequenceSlice()
            s.add_slice(sequence[begin:end], idmaker.next_unique_id(), begin, end, pset)
            slices.append(s)
        self.slices = slices
        # todo, actually split up sequence


class SequenceSlice(GenericData):
    def __init__(self):
        super().__init__()
        self.spec += [('sequence', True, list, None),
                      ('start', True, int, None),
                      ('end', True, int, None),
                      ('processing_set', True, str, None),
                      ('slice_id', True, str, None)]
        self.start = None
        self.end = None
        self.sequence = []
        self.processing_set = None
        self.slice_id = None

    def add_sequence(self, sequence, seqid):
        self.sequence = list(chunk_str(sequence, 100))
        self.slice_id = seqid
        self.start = 0  # todo, -> 0
        self.end = len(sequence)

    def add_slice(self, sequence, slice_id, start, end, processing_set):
        self.sequence = list(chunk_str(sequence, 100))
        self.slice_id = slice_id
        self.start = start
        self.end = end
        self.processing_set = processing_set

    def to_example(self):
        raise NotImplementedError


class MetaInfoSeqLike(GenericData):
    def __init__(self):
        super().__init__()
        self.spec += [('total_bp', True, int, None),
                      ('gc_content', True, int, None),
                      ('cannonical_kmer_content', True, dict, None),
                      ('ambiguous_content', True, int, None)]
        # will be calculated as sequences are added
        self.total_bp = 0
        self.gc_content = 0
        self.cannonical_kmer_content = {}
        self.ambiguous_content = 0


class MetaInfoGenome(MetaInfoSeqLike):
    def __init__(self):
        super().__init__()
        self.spec += [('species', True, str, None),
                      ('accession', True, str, None),
                      ('version', True, str, None),
                      ('acquired_from', True, str, None)]
        # will need to be set
        self.species = ""
        self.accession = ""
        self.version = ""
        self.acquired_from = ""

    def maybe_add_info_from_fasta(self, fasta):
        # todo, could possibly parse out version info or accession or whatever??
        if not self.species:
            self.species = self.species_from_path(fasta)

    @staticmethod
    def species_from_path(fasta_path):
        if '/' in fasta_path:
            fasta_path = fasta_path.split('/')[-1]
        if fasta_path.endswith('.fasta'):
            species = fasta_path.replace('.fasta', '')
        elif fasta_path.endswith('.fa'):
            species = fasta_path.replace('.fa', '')
        else:
            species = fasta_path
        return species

    def add_sequence_meta_info(self, seq_met_info):
        self.total_bp += seq_met_info.total_bp
        self.gc_content += seq_met_info.gc_content
        self.cannonical_kmer_content = add_paired_dictionaries(self.cannonical_kmer_content,
                                                               seq_met_info.cannonical_kmer_content)
        self.ambiguous_content += seq_met_info.ambiguous_content


class MetaInfoSequence(MetaInfoSeqLike):
    def __init__(self):
        super().__init__()
        self.spec += [("deprecated_header", True, str, None),
                      ("seqid", True, str, None)]
        self.deprecated_header = ""
        self.seqid = ""

    def add_sequence(self, fasta_header, sequence, id_delim=' ', smallest_mer=2, largest_mer=2):
        self.deprecated_header = fasta_header
        self.seqid = fasta_header.split(id_delim)[0]
        self.total_bp = len(sequence)
        self.calculate_and_set_meta(sequence.lower(), smallest_mer, largest_mer)

    def calculate_and_set_meta(self, sequence, smallest_mer, largest_mer):
        # go through all the bp and count all the things
        gc = 0
        gc_bps = ('g', 'c')
        ambiguous = 0
        known_bps = ('a', 't', 'c', 'g')
        for bp in sequence:
            if bp.lower() in gc_bps:
                gc += 1
            if bp not in known_bps:
                ambiguous += 1
        # count all kmers
        self.cannonical_kmer_content = {}
        for k in range(smallest_mer, largest_mer + 1):
            mer_counter = MerCounter(k)
            mer_counter.add_sequence(sequence)
            self.cannonical_kmer_content[str(k)] = mer_counter.export()
        # record
        self.gc_content = gc
        self.ambiguous_content = ambiguous


class MerCounter(object):
    amb = 'ambiguous_mers'

    def __init__(self, k):
        self.k = k
        self.counts = {}
        # calculate all possible mers of this length, and set counter to 0
        for mer in itertools.product('atcg', repeat=k):
            mer = ''.join(mer)
            self.counts[mer] = 0
        self.counts[MerCounter.amb] = 0
        # most recent base pairs of up to length k
        self.sliding_mer = []

    def add_mer(self, mer):
        try:
            self.counts[mer] += 1
        except KeyError:
            self.counts[MerCounter.amb] += 1

    def export(self):
        out = copy.deepcopy(self.counts)
        for key in self.counts:
            if key != MerCounter.amb:
                # collapse to cannonical kmers
                rc_key = reverse_complement(key)
                if key != min(key, rc_key):
                    out[rc_key] += out[key]
                    out.pop(key)
        return out

    def add_sequence(self, sequence):
        for mer in gen_mers(sequence, self.k):
            self.add_mer(mer)


def gen_mers(sequence, k):
    for i in range(len(sequence) - k + 1):
        yield sequence[i:(i+k)]


def reverse_complement(seq):
    fw = "ACGTMRWSYKVHDBN"
    rv = "TGCAKYWSRMBDHVN"
    fw += fw.lower()
    rv += rv.lower()
    key = {}
    for f, r in zip(fw, rv):
        key[f] = r
    rc_seq = ''
    for base in reversed(seq):
        try:
            rc_seq += key[base]
        except KeyError as e:
            raise KeyError('{} caused by non DNA character {}'.format(e, base))

    return rc_seq


def chunk_str(string, length):
    for i in range(0, len(string), length):
        yield string[i:(i+length)]


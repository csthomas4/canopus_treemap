# parse chem ontology
import urllib.request
import zipfile
from io import BytesIO
import re
from pathlib import Path
import numpy as np
import json

class Ontology(object):
    
    def __init__(self, categories):
        self.categories = categories
        self.root = make_tree(categories)

class Category(object):
    
    def __init__(self, oid, name, description, parent_oid):
        self.oid = oid
        self.name = name
        self.description = description
        self.parent_oid = parent_oid
        if self.parent_oid.startswith('-1'):
            self.parent_oid = None
        self.children = []
        self.parent = None
        
    def ancestors(self):
        node = self
        xs = []
        while (node.parent is not None):
            xs.append(node.parent)
            node = node.parent
        return xs
        
        
        
    def to_tsv(self):
        return "\t".join((self.oid, self.name, self.description, self.parent_oid if self.parent_oid is not None else "-1"))

def write_ontology(ontology, filename):
    print("write into file")
    with filename.open("w") as fhandle: 
        for category in ontology.categories.values():
            fhandle.write(category.to_tsv())
            fhandle.write("\n")
        
    
def ontology_from_tsv(filename):
    categories = {}
    with filename.open() as fhandle:
        for line in fhandle:
            line = line.rstrip()
            (oid,name,description,parent_oid) = line.split("\t")
            categories[oid] = Category(oid,name,description,parent_oid)
    return Ontology(categories) 
            

def make_tree(categories):
    for category in categories.values():
        if category.parent_oid:
            categories[category.parent_oid].children.append(category)
            category.parent = categories[category.parent_oid]
    for category in categories.values():
        if category.parent is None:
            return category
        
def download_ontology():
    categories = dict()
    def makeCat(d):
        if d:
            categories[d["oid"]] = Category(d["oid"],d["name"],d["description"],d["parent_oid"] if "parent_oid" in d else "-1")
        return dict()
        
    r = urllib.request.urlopen("http://classyfire.wishartlab.com/system/downloads/1_0/chemont/ChemOnt_2_1.obo.zip").read()
    descr_reg = re.compile(r"def:\s*\"(.+)\"\s*\[")
    parent_reg = re.compile(r"is_a:\s*(CHEMONTID:\d+)\s*!.+")
    dummy = dict()
    with zipfile.ZipFile(BytesIO(r)) as z:
        with z.open('ChemOnt_2_1.obo') as ont:
            for line in ont.read().decode("utf-8").splitlines():
                if line.startswith("[Term]"):
                    dummy = makeCat(dummy)
                if line.startswith("id:"):
                    dummy["oid"] = line.split(": ")[1]
                elif line.startswith("def:"):
                    m = descr_reg.match(line)
                    dummy["description"] = m.group(1)
                elif line.startswith("is_a"):
                    m=parent_reg.match(line)
                    dummy["parent_oid"] = m.group(1)
                elif line.startswith("name:"):
                    dummy["name"] = line.split(": ")[1]
            makeCat(dummy)
    return Ontology(categories)

def load_ontology():
    path = Path("chemontology.csv")
    if path.exists():
        return ontology_from_tsv(path)
    else:
        ontology = download_ontology()
        write_ontology(ontology, path)
        return ontology

class Compound(object):
    def __init__(self, name, directory):
        self.name = name
        self.directory = directory
        self.canopusfp = None

def extract_leafs(setofcompounds):
    innerNodes = set()
    for node in setofcompounds:
        innerNodes.update(node.ancestors())
    return setofcompounds - innerNodes

class CanopusStatistics(object):
    def __init__(self, workspace, quantifier=None):
        if quantifier is None:
            self.quantifier = lambda x: 1
        else:
            self.quantifier = quantifier
        self.workspace = workspace
        self.probabilistic_counts = self.__category_counts__()
        self.reduced_counts = self.__category_counts__()
        self.total_count = 0.0
        
    def setCompounds(self, compounds):
        self.compounds = compounds
        self.make_probabilistic_category_statistics()
        self.make_class_counting_statistics()
        
    def assign_most_specific_classes(self, stats=None):
        if stats is None:
            stats = self.counting
        # always decide for the most specific compound category
        assignment = dict()
        for compound in self.compounds_with_fingerprints():
            assignment[compound] = min(self.leafs(compound), default=self.workspace.ontology.root, key=lambda x: stats[x])
        self.assignments = assignment 
        reduced_counts = self.__category_counts__()
        for assignment in self.assignments.values():
            reduced_counts[assignment] += 1
            for ancestor in assignment.ancestors():
                reduced_counts[ancestor] += 1
        self.reduced_counts = reduced_counts
            
    
    def leafs(self, compound, threshold=0.5):
        compoundset = set()
        for index, probability in enumerate(compound.canopusfp):
            if probability >= threshold:
                category = self.workspace.mapping[index]
                compoundset.add(category)
        return extract_leafs(compoundset)

    def categoriesFor(self, compound, threshold):
        compoundset = set()
        for index, probability in enumerate(compound.canopusfp):
            if probability >= threshold:
                category = self.workspace.mapping[index]
                while not (category is None) and not (category in compoundset):
                    compoundset.add(category)
                    category = category.parent
        return compoundset

    
    def make_class_counting_statistics(self, threshold=0.5):
        counting = self.__category_counts__()
        summe = 0
        for compound in self.compounds_with_fingerprints():
            summe += self.quantifier(compound)
            for node in self.categoriesFor(compound, threshold):
                counting[node] += self.quantifier(compound)
        self.counting = counting
        self.total_count = summe
        
    def make_probabilistic_category_statistics(self):
        self.probabilistic_counts = self.__category_counts__()
        summe = 0.0
        for compound in self.compounds_with_fingerprints():
            summe += self.quantifier(compound)
            for index, probability in enumerate(compound.canopusfp):
                if probability >= 0.01:
                    category = self.workspace.mapping[index]
                    self.probabilistic_counts[category] += (probability * self.quantifier(compound))
        self.probabilistic_counts[self.workspace.ontology.root] = summe
        
    def compounds_with_fingerprints(self):
        return [compound for compound in self.compounds.values() if compound.canopusfp is not None]
        
        
    def __category_counts__(self):
        counts = dict()
        for category in self.workspace.ontology.categories.values():
            counts[category] = 0
        return counts

class SiriusWorkspace(object):
    def __init__(self, rootdir,ontology=None):
        self.rootdir = Path(rootdir)
        self.compounds = dict()
        self.ontology = load_ontology() if ontology is None else ontology
        if Path(rootdir).is_dir():
            self.load_ontology_index()
            self.load_compounds()
        else:
            self.load_compounds_from_csv(rootdir)
        self.statistics = CanopusStatistics(self)
        self.statistics.setCompounds(self.compounds)
        self.statistics.assign_most_specific_classes()

    def write_csv(self, filename):
        with open(filename,"w") as fhandle:
            fhandle.write("name\tchemontid\tcount\tfrequency\treducedFrequency\n")
            for category in self.ontology.categories.values():
                fhandle.write("%s\t%s\t%d\t%f\t%f\n" % (category.name, category.oid, self.statistics.counting[category], self.statistics.counting[category]/len(self.statistics.compounds), self.statistics.reduced_counts[category] /len(self.statistics.compounds)))

    def quantify(self, quantifier):
        s=CanopusStatistics(self, quantifier=quantifier)
        s.setCompounds(self.compounds)
        s.assign_most_specific_classes(self.statistics.counting)
        return s

    def select(self, compoundset):
        s=CanopusStatistics(self)
        s.setCompounds(compoundset)
        s.assign_most_specific_classes(self.statistics.counting)
        return s
        
    def selectByNames(self, names):
        aset = frozenset(names)
        return self.select({n:c for (n,c) in self.compounds.items() if n in names})
        
        
    def selectByRegexp(self, reg):
        r = re.compile(reg)
        return self.select({n:c for (n,c) in self.compounds.items() if re.match(r, n)})
        
    def json_treemap(self, stats=None, use_probabilities=True):
        if stats is None:
            stats = self.statistics
        return self.__node_to_json(self.ontology.root, stats, use_probabilities)
        
    def __node_to_json(self, node, stats, use_probabilities):
        num = stats.probabilistic_counts[node] if use_probabilities else stats.counting[node]
        freq = num/stats.total_count
        return {"name": node.name, "description": node.description, 
         "freq": freq, 
         "num": num, 
                "size": stats.reduced_counts[node],
        "children": [self.__node_to_json(child,stats,use_probabilities) for child in node.children if child in stats.reduced_counts and stats.reduced_counts[child]>0]}
        
    def load_compounds_from_csv(self, csvFile):
        header = None
        with open(csvFile) as fhandle:
            for line in fhandle:
                cols = line.rstrip().split("\t")
                if header:
                    cmp = Compound(cols[0],None)
                    cmp.canopusfp = np.array([float(x) for x in cols[1:]])
                    self.compounds[cmp.name] = cmp
                else:
                    header = cols
                    self.create_mapping_from_tsv(cols)

    def create_mapping_from_tsv(self, columns):
        mapping = dict()
        ontologyByName = dict()
        for category in self.ontology.categories:
            c=self.ontology.categories[category]
            ontologyByName[c.name] = c
        for (index, name) in enumerate(columns[1:]):
            mapping[index] = ontologyByName[name]
        self.mapping = mapping
        return mapping

    def load_compounds(self):
        for adir in Path(self.rootdir).glob("*/spectrum.ms"):
            compound_dir = adir.parent
            try:
                canopusfp = next(compound_dir.glob("canopus/1_*.fpt"))
                name = None
                with adir.open() as fhandle:
                    for line in fhandle:
                        name = line.strip().split(" ")[1]
                        break
                cmp = Compound(name, adir)
                if canopusfp.exists():
                    cmp.canopusfp = np.loadtxt(canopusfp)
                self.compounds[name] = cmp
            except StopIteration:
                pass
        
        
    def load_ontology_index(self):
        mapping = dict()
        with Path(self.rootdir, "canopus.csv").open() as fhandle:
            skipheader=True
            for line in fhandle:
                if skipheader:
                    skipheader = False
                else:
                    cols = line.rstrip().split("\t")
                    oid = cols[3].replace("CHEMONT:","CHEMONTID:")
                    mapping[int(cols[0])] = self.ontology.categories[oid]
        self.mapping = mapping
        return mapping
                
                    

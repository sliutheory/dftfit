from pymatgen.io.cif import CifParser
from pymatgen.io.vasp import Poscar
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer
from pymatgen.core import Structure

from .utils import is_file_type, is_not_file_type
from ..potential import Potential
from ..predict import Predict
from ..predict.utils import print_elastic_information


def add_subcommand_test(subparsers):
    parser = subparsers.add_parser('test', help='train potential model')
    sub_subparsers = parser.add_subparsers()
    add_subcommand_test_properties(sub_subparsers)
    add_subcommand_test_relax(sub_subparsers)


def add_subcommand_test_properties(subparsers):
    parser = subparsers.add_parser('properties', help='calculate properties of structure with potential')
    parser.set_defaults(func=handle_subcommand_test_properties)
    parser.add_argument('-p', '--potential', help='potential filename in in yaml/json format', type=is_file_type, required=True)
    parser.add_argument('-s', '--structure', help='base structure', type=is_file_type, required=True)
    parser.add_argument('--property', action='append', choices=['lattice', 'elastic', 'static'], help='choose properties to test default is all')
    parser.add_argument('--software', default='lammps', help='md calculator to use')
    parser.add_argument('--command', help='md calculator command has sensible defaults')
    parser.add_argument('--num-workers', default=1, type=int, help='number md calculators to use')


def add_subcommand_test_relax(subparsers):
    parser = subparsers.add_parser('relax', help='calculate relaxed structure from potential')
    parser.set_defaults(func=handle_subcommand_test_relax)
    parser.add_argument('-p', '--potential', help='potential filename in in yaml/json format', type=is_file_type, required=True)
    parser.add_argument('-s', '--structure', help='base structure', type=is_file_type, required=True)
    parser.add_argument('--software', default='lammps', help='md calculator to use')
    parser.add_argument('--command', help='md calculator command has sensible defaults')
    parser.add_argument('-o', '--output-filename', type=is_not_file_type, help='filename to write relaxed structure', required=True)


def get_structure(filename):
    if filename.lower().endswith('.cif'):
        return CifParser(filename).get_structures()[0]
    elif filename.lower().endswith('poscar'):
        return Poscar.from_file(filename).structure
    else:
        raise ValueError('Cannot determine file type from filename [.cif, poscar]')


def handle_subcommand_test_properties(args):
    properties = set(args.property) if args.property else {'elastic', 'lattice', 'static'}
    default_commands = {
        'lammps': 'lammps'
    }
    command = args.command if args.command else default_commands.get(args.software)
    predict = Predict(calculator=args.software, command=command, num_workers=args.num_workers)
    potential = Potential.from_file(args.potential)
    structure = get_structure(args.structure)

    import warnings
    warnings.filterwarnings("ignore") # yes I have sinned

    if 'lattice' in properties:
        old_lattice, new_lattice = predict.lattice_constant(structure, potential)
        print('\nLattice Constants:')
        print('        a: {:6.3f}    b: {:6.3f}     c: {:6.3f}'.format(*new_lattice.abc))
        print('    alpha: {:6.3f} beta: {:6.3f} gamma: {:6.3f}'.format(*new_lattice.angles))
    if 'elastic' in properties:
        elastic = predict.elastic_constant(structure, potential)
        print('\nElastic:')
        print_elastic_information(elastic)

    if 'static' in properties:
        static = predict.static(structure, potential)
        print('\nStatic:')
        print('    Energy: [eV]')
        print('      {:16.3f}'.format(static['energy']))
        print('    Forces: [eV/Angstrom]')
        for row in static['forces']:
            print('      {:16.3f} {:16.3f} {:16.3f}'.format(*row))
        print('    Stress: [bars]')
        for row in static['stress']:
            print('      {:16.3f} {:16.3f} {:16.3f}'.format(*row))


def handle_subcommand_test_relax(args):
    default_commands = {
        'lammps': 'lammps'
    }
    command = args.command if args.command else default_commands.get(args.software)
    predict = Predict(calculator=args.software, command=command, num_workers=1)
    potential = Potential.from_file(args.potential)
    structure = get_structure(args.structure)

    import warnings
    warnings.filterwarnings("ignore") # yes I have sinned

    sga = SpacegroupAnalyzer(structure)
    conventional_structure = sga.get_conventional_standard_structure()
    old_lattice, new_lattice = predict.lattice_constant(conventional_structure, potential)
    equilibrium_structure = Structure(
        new_lattice,
        [s.specie.element for s in conventional_structure.sites],
        [s.frac_coords for s in conventional_structure.sites])
    equilibrium_structure.to(filename=args.output_filename)

import math
import itertools

import lammps
from lammps.potential import (
    write_table_pair_potential,
    write_tersoff_potential,
    write_stillinger_weber_potential,
    write_gao_weber_potential
)
import numpy as np

from .base import DFTFITCalculator, MDReader


class LammpsCythonDFTFITCalculator(DFTFITCalculator):
    """This is not a general purpose lammps calculator. Only for dftfit
    evaluations. For now there are not plans to generalize it.
    """
    def __init__(self, structures, num_workers=1):
        if num_workers != 1:
            raise NotImplemented('For now limited to one worker')
        self.structures = structures
        self.elements = []
        self.lammps_systems = []

    def _initialize_lammps(self, structure):
        lmp = lammps.Lammps(units='metal', style='full', args=[
            '-log', 'none', '-screen', 'none'
        ])
        lmp.system.add_pymatgen_structure(structure, self.elements)
        lmp.thermo.add('my_ke', 'ke', 'all')
        return lmp

    async def create(self):
        # ensure element indexes are the same between all lammps calculations
        self.elements = set()
        for structure in self.structures:
            self.elements = self.elements | set(structure.species)
        self.elements = list(self.elements)

        for structure in self.structures:
            self.lammps_systems.append(self._initialize_lammps(structure))

    def _apply_potential(self, lmp, potential):
        lammps_commands = write_potential(potential, elements=self.elements, unique_id=1)
        for command in lammps_commands:
            lmp.command(command)

    def _apply_potential_files(self, potential):
        lammps_files = write_potential_files(potential, elements=self.elements, unique_id=1)
        for filename, content in lammps_files.items():
            with open(filename, 'w') as f:
                f.write(content)

    async def submit(self, potential, properties=None):
        properties = properties or {'stress', 'energy', 'forces'}
        results = []
        self._apply_potential_files(potential)
        for lmp, structure in zip(self.lammps_systems, self.structures):
            self._apply_potential(lmp, potential)
            lmp.run(0)
            S = lmp.thermo.computes['thermo_press'].vector
            results.append(MDReader(
                forces=lmp.system.forces.copy(),
                energy=lmp.thermo.computes['thermo_pe'].scalar + lmp.thermo.computes['my_ke'].scalar,
                stress=np.array([
                    [S[0], S[3], S[5]],
                    [S[3], S[1], S[4]],
                    [S[5], S[4], S[2]]
                ]),
                structure=structure))
        return results

    def shutdown(self):
        pass


def tersoff_2_to_tersoff(element_parameters, mixing_parameters):
    def mixing_params_from_singles(e1, e2, mixing_value):
        p1 = [float(_) for _ in element_parameters[e1]]
        p2 = [float(_) for _ in element_parameters[e2]]
        mixing = float(mixing_value)
        return [
            3.0,                                # m
            1.0,                                # gamma
            0.0,                                # lambda3
            p1[0],                              # c
            p1[1],                              # d
            p1[2],                              # costheta0
            p1[3],                              # n
            p1[4],                              # beta
            (p1[5] + p2[5]) / 2,                # lambda2
            mixing * math.sqrt(p1[6] * p2[6]),  # B
            math.sqrt(p1[7] * p2[7]),           # R
            math.sqrt(p1[8] * p2[8]),           # D
            (p1[9] + p2[9]) / 2,                # lambda1
            math.sqrt(p1[10] * p2[10]),         # A
        ]

    parameters = {}
    for e1, e2, e3 in itertools.product(element_parameters, repeat=3):
        if e1 == e2:
            mixing_value = 1.0
        else:
            sorted_e1_e2 = tuple(sorted([e1, e2]))
            mixing_value = mixing_parameters.get(sorted_e1_e2)
            if mixing_value is None:
                continue
        parameters[(e1, e2, e3)] = mixing_params_from_singles(e1, e2, mixing_value)
    return parameters


def write_potential_files(potential, elements, unique_id=1):
    """Generate lammps files required by specified potential

    Parameters
    ----------
    potential: dftfit.potential.Potential
        schema representation of potential
    elements: list
        list specifying the index of each element
    unique_id: int
        an id that can be used for files to guarentee uniqueness
    """
    spec = potential.schema['spec']
    lammps_files = {}
    for i, pair_potential in enumerate(spec.get('pair', [])):
        if pair_potential['type'] == 'tersoff-2':
            element_parameters = {}
            mixing_parameters = {}
            for parameter in pair_potential['parameters']:
                if len(parameter['elements']) == 1:
                    element_parameters[parameter['elements'][0]] = parameter['coefficients']
                elif len(parameter['elements']) == 2:
                    mixing_parameters[tuple(sorted(parameter['elements']))] = parameter['coefficients'][0]
            parameters = tersoff_2_to_tersoff(element_parameters, mixing_parameters)
        elif pair_potential['type'] in {'tersoff', 'stillinger-weber', 'gao-weber'}:
            parameters = {}
            for parameter in pair_potential['parameters']:
                parameters[tuple(parameter['elements'])] = parameter['coefficients']

        if pair_potential['type'] in {'tersoff-2', 'tersoff'}:
            filename = '/tmp/lammps.%d.%d.tersoff' % (i, unique_id)
            lammps_files[filename] = write_tersoff_potential(parameters)
        elif pair_potential['type'] == 'stillinger-weber':
            filename = '/tmp/lammps.%d.%d.sw' % (i, unique_id)
            lammps_files[filename] = write_stillinger_weber_potential(parameters)
        elif pair_potential['type'] == 'gao-weber':
            filename = '/tmp/lammps.%d.%d.gw' % (i, unique_id)
            lammps_files[filename] = write_gao_weber_potential(parameters)
    return lammps_files


def write_potential(potential, elements, unique_id=1):
    """Generate lammps commands required by specified potential

    Parameters
    ----------
    potential: dftfit.potential.Potential
        schema representation of potential
    elements: list
        list specifying the index of each element
    unique_id: int
        an id that can be used for files to guarentee uniqueness

    Supported Potentials:

    - two-body
      - lennard jones
      - buckingham
    - three-body
      - tersoff-2, tersoff
      - stillinger-weber
      - gao-weber
    - n-body
      - coloumb charge (long + short range)
    """
    spec = potential.schema['spec']
    element_map = {e.symbol: i for i, e in enumerate(elements, start=1)}

    # collect potentials in spec
    potentials = []
    lammps_commands = []
    if ('charge' in spec) and ('kspace' in spec):
        lammps_commands.append('kspace_style %s %f' % (spec['kspace']['type'], spec['kspace']['tollerance']))
        for element, charge in spec['charge'].items():
            lammps_commands.append('set type %d charge %f' % (element_map[element], float(charge)))
        potentials.append(({
            'pair_style': 'coul/long %f' % 10.0,
            'pair_coeff': [('* *', 'coul/long', '')]
        }))

    for i, pair_potential in enumerate(spec.get('pair', [])):
        if pair_potential['type'] == 'buckingham':
            pair_coeffs = []
            for parameter in pair_potential['parameters']:
                ij = ' '.join([str(_) for _ in sorted([
                    element_map[parameter['elements'][0]],
                    element_map[parameter['elements'][1]]])])
                pair_coeffs.append((ij, 'buck', ' '.join([str(float(coeff)) for coeff in parameter['coefficients']])))
            potentials.append({
                'pair_style': 'buck %f' % pair_potential.get('cutoff', [10.0])[-1],
                'pair_coeff': pair_coeffs
            })
        elif pair_potential['type'] == 'tersoff-2':
            filename = '/tmp/lammps.%d.%d.tersoff' % (i, unique_id)
            potentials.append({
                'pair_style': 'tersoff',
                'pair_coeff': [('* *', 'tersoff', '%s %s' % (
                    filename, ' '.join(str(e) for e in elements)))],
            })
        elif pair_potential['type'] == 'tersoff':
            filename = '/tmp/lammps.%d.%d.tersoff' % (i, unique_id)
            potentials.append({
                'pair_style': 'tersoff',
                'pair_coeff': [('* *', 'tersoff', '%s %s' % (
                    filename, ' '.join(str(e) for e in elements)))],
            })
        elif pair_potential['type'] == 'stillinger-weber':
            filename = '/tmp/lammps.%d.%d.sw' % (i, unique_id)
            potentials.append({
                'pair_style': 'sw',
                'pair_coeff': [('* *', 'sw', '%s %s' % (
                    filename, ' '.join(str(e) for e in elements)))],
            })
        elif pair_potential['type'] == 'gao-weber':
            filename = '/tmp/lammps.%d.gw' % (i, unique_id)
            potentials.append({
                'pair_style': 'gw',
                'pair_coeff': [('* *', 'gw', '%s %s' % (
                    filename, ' '.join(str(e) for e in elements)))],
            })
        else:
            raise ValueError('pair potential %s not implemented yet!' % (
                pair_potential['type']))

    if len(potentials) == 1:  # no need for hybrid/overlay
        potential = potentials[0]
        lammps_commands.append('pair_style %s' % potential['pair_style'])
        for pair_coeff in potential['pair_coeff']:
            lammps_commands.append('pair_coeff ' + pair_coeff[0] + ' ' + pair_coeff[2])
    elif len(potentials) > 1:  # use hybrid/overlay to join all potentials
        lammps_commands.append('pair_style hybrid/overlay ' + ' '.join(potential['pair_style'] for potential in potentials))
        for potential in potentials:
            for pair_coeff in potential.get('pair_coeff', []):
                lammps_commands.append('pair_coeff ' + ' '.join(pair_coeff))
    return lammps_commands

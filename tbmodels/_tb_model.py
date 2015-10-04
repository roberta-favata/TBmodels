#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Author:  Dominik Gresch <greschd@gmx.ch>
# Date:    02.06.2015 17:50:33 CEST
# File:    _tb_model.py

from __future__ import division, print_function

from ._bare_model import BareModel
from mtools.bands import EigenVal

import copy
import warnings
import decorator
import itertools
import numpy as np
import scipy.linalg as la
import scipy.sparse as sparse

class Model(object):
    # I will patch the sparse array classes to have array properties.
    def __init__(self, hoppings, occ=None, pos=None, uc=None):
        # reading in hopping matrices
        if len(hoppings) == 0:
            raise ValueError('Empty hoppings dictionary supplied.')
        self.hoppings = {tuple(key): np.array(value) for key, value in G_dict.items()}
        self.size = self._G_dict.items()[0].shape[0]
        for h_array in self.hoppings.values():
            if not h_array.shape() == (self.size, self.size):
                raise ValueError('Hopping matrices of inconsistent sizes supplied.')
                
        # positions and the unit cell
        if pos is None:
            self.pos = [np.array([0., 0., 0.]) for _ in range(self.size)]
        if uc is None:
            self.uc = None
        else:
            self.uc = np.array(uc)

        # number of occupied states
        self.occ = None if (occ is None) else int(occ)

    def add_hop(self, hop):
        """
        Adds additional hopping terms. This can be useful for example if the Model was created as a :class:`HrModel` , but additional terms such as spin-orbit coupling need to be added.
        """
        self._hop.extend(hop)

    def hamilton(self, k):
        """
        Creates the Hamiltonian matrix.

        :param k:   k-point
        :type k:    list

        :returns:   2D numpy array
        """
        return self.bare_model.hamilton(k)

    def eigenval(self, k):
        """
        Returns the eigenvalues at a given k point.

        :param k:   k-point
        :type k:    list

        :returns:   list of EigenVal objects
        """
        return self.bare_model.eigenval(k)


    def to_hr(self):
        raise NotImplementedError # TODO

    #-------------------CREATING DERIVED MODELS-------------------------#
    #~ def mixing(self, mixin_model, mixin_strength, in_place=False):
        #~ """
        #~ Creates an interpolation between this model and a given other model. For this interpolation to have any physical sense, the bases w.r.t. which the two models are defined must be identical.
        #~ """
        #~ raise NotImplementedError

    
    def __add__(self, model):
        if not isinstance(model, Model):
            raise ValueError('Invalid argument type for Model.__add__: {}'.format(type(model)))

        # ---- consistency checks ----
        # check if the occupation number matches
        if self.occ != model.occ:
            raise ValueError('Error when adding Models: occupation numbers ({0}, {1}) don\'t match'.format(self.occ, model.occ))

        # check if the number of orbitals matches
        if len(self._on_site) != len(model._on_site):
            raise ValueError('Error when adding Models: the number of orbitals ({0}, {1}) doesn\'t match'.format(len(self._on_site), len(model._on_site)))

        # TODO: maybe use TolerantTuple for this purpose
        # check if the unit cells match
        uc_match = True
        if self._uc is None or model._uc is None:
            if model._uc is not self._uc:
                uc_match = False
        else:
            tolerance = 1e-6
            for v1, v2 in zip(self._uc, model._uc):
                if not uc_match:
                    break
                for x1, x2 in zip(v1, v2):
                    if abs(x1 - x2) > tolerance:
                        uc_match = False
                        break
        if not uc_match:
            raise ValueError('Error when adding Models: unit cells don\'t match.\nModel 1: {0}\nModel 2: {1}'.format(self._uc, model._uc))

        # check if the positions match
        pos_match = True
        tolerance = 1e-6
        for v1, v2 in zip(self.pos, model.pos):
            if not pos_match:
                break
            for x1, x2 in zip(v1, v2):
                if abs(x1 - x2) > tolerance:
                    pos_match = False
                    break
        if not pos_match:
            raise ValueError('Error when adding Models: positions don\'t match.\nModel 1: {0}\nModel 2: {1}'.format(self.pos, model.pos))

        # ---- main part ----
        new_occ = self.occ
        new_pos = copy.deepcopy(self.pos)
        new_on_site = [e1 + e2 for e1, e2 in zip(self._on_site, model._on_site)]
        new_uc = copy.deepcopy(self._uc)
        new_hop = []
        for hop in self._hop:
            new_hop.append(copy.deepcopy(hop))
        for hop in model._hop:
            new_hop.append(copy.deepcopy(hop))
        # -------------------
        return self._create_model(in_place=False, on_site=new_on_site, pos=new_pos, hop=new_hop, occ=new_occ, add_cc=False, uc=new_uc)

    def __radd__(self, model):
        """
        Addition is commutative.
        """
        return self.__add__(model)

    def __mul__(self, x):
        """
        Multiply on-site energies and hopping parameter strengths by a constant factor.
        """
        new_occ = self.occ
        new_pos = copy.deepcopy(self.pos)
        new_on_site = [energy * x for energy in self._on_site]
        new_uc = copy.deepcopy(self._uc)
        new_hop = []
        for i0, i1, G, t in self._hop:
            new_hop.append([i0, i1, G, t * x])

        return self._create_model(in_place=False, on_site=new_on_site, pos=new_pos, hop=new_hop, occ=new_occ, add_cc=False, uc=new_uc)

    def __rmul__(self, x):
        """
        Multiplication with constant factors is commutative.
        """
        return self.__mul__(x)

    @_modifying
    def supercell(self, dim, periodic=[True, True, True], passivation=None, in_place=False):
        r"""
        Creates a tight-binding model which describes a supercell.

        :param dim: The dimensions of the supercell in terms of the previous unit cell.
        :type dim:  list(int)

        :param periodic:    Determines whether periodicity is kept in each crystal direction. If not (entry is ``False``), hopping terms that go across the border of the supercell (in the given direction) are cut.
        :type periodic:     list(bool)

        :param passivation: Determines the passivation on the surface layers. It must be a function taking three input variables ``x, y, z``, which are lists ``[bottom, top]`` of booleans indicating whether a given unit cell inside the supercell touches the bottom and top edge in the given direction. The function returns a list of on-site energies (must be the same length as the initial number of orbitals) determining the passivation strength in said unit cell.
        :type passivation:  function

        :param in_place:    Determines whether the current model is modified (``in_place=True``) or a new model is returned, preserving the current one (``in_place=False``, default).
        :type in_place:     bool
        """
        nx, ny, nz = dim
        dim = np.array(dim, dtype=int)

        new_occ = sum(dim) * self.occ
        if self._uc is None:
            new_uc = None
        else:
            new_uc = self._uc * dim

        # the new positions, normalized to the supercell
        new_pos = []
        reduced_pos = [p / dim for p in self.pos]
        for i in range(nx):
            for j in range(ny):
                for k in range(nz):
                    tmp_offset = np.array([i, j, k]) / dim
                    for p in reduced_pos:
                        new_pos.append(tmp_offset + p)

        # new hoppings, cutting those that cross the supercell boundary
        # in a non-periodic direction
        new_hop = []
        #~ cut_hop_list = np.zeros(len(self._on_site) * nx * ny * nz) # FOR AUTO-PASSIVATION
        # full index of an orbital in unit cell at uc_pos
        def full_idx(uc_pos, orbital_idx):
            """
            Computes the full index of an orbital in a given unit cell.
            """
            uc_idx = _pos_to_idx(uc_pos, dim)
            return uc_idx * len(self._on_site) + orbital_idx
        for i in range(nx):
            for j in range(ny):
                for k in range(nz):
                    uc0_pos = np.array([i, j, k], dtype=int)
                    for i0, i1, G, t in self._hop:
                        # new index of orbital 0
                        new_i0 = full_idx(uc0_pos, i0)
                        # position of the uc of orbital 1, not mapped inside supercell
                        full_uc1_pos = uc0_pos + G
                        outside_supercell = [(p < 0) or (p >= d) for p, d in zip(full_uc1_pos, dim)]
                        # test if the hopping should be cut
                        cut_hop = any([not per and outside for per, outside in zip(periodic, outside_supercell)])
                        if cut_hop:
                            #~ # FOR AUTO-PASSIVATION
                            #~ cut_hop_list[new_i0] += abs(t)**2
                            #~ # END AUTO-PASSIVATION
                            continue
                        else:
                            # G in terms of supercells
                            new_G = np.array(np.floor(full_uc1_pos / dim), dtype=int)
                            # mapped into the supercell
                            uc1_pos = full_uc1_pos % dim
                            new_i1 = full_idx(uc1_pos, i1)
                            new_hop.append([new_i0, new_i1, new_G, t])
        #~ # FOR AUTO-PASSIVATION
        #~ for i in list(reversed(np.argsort(cut_hop_list)))[:28]:
            #~ print('orbital {}, uc no. {}, cut_t={}'.format(i % len(self._on_site), i // len(self._on_site), cut_hop_list[i]))
        #~ # END AUTO-PASSIVATION

        # new on_site terms, including passivation
        if passivation is None:
            passivation = lambda x, y, z: np.zeros(len(self._on_site))
        new_on_site = []
        for i in range(nx):
            for j in range(ny):
                for k in range(nz):
                    tmp_on_site = copy.deepcopy(self._on_site)
                    tmp_on_site += np.array(passivation(*_edge_detect_pos([i, j, k], dim)), dtype=float)
                    new_on_site.extend(tmp_on_site)
        return self._create_model(in_place, on_site=new_on_site, pos=new_pos, hop=new_hop, occ=new_occ, add_cc=False, uc=new_uc)


    @_modifying
    def trs(self, in_place=False):
        """
        Adds a time-reversal image of the current model.

        :param in_place:    Determines whether the current model is modified (``in_place=True``) or a new model is returned, preserving the current one (``in_place=False``, default).
        :type in_place:     bool
        """
        new_occ = self.occ * 2
        new_pos = np.vstack([self.pos, self.pos])
        new_on_site = np.hstack([self._on_site, self._on_site])
        new_hop = copy.deepcopy(self._hop)
        idx_offset = len(self._on_site)
        for i0, i1, G, t in self._hop:
            # here you can either do -G or t.conjugate(), but not both
            new_hop.append([i1 + idx_offset, i0 + idx_offset, -G, t])
        return self._create_model(in_place, on_site=new_on_site, pos=new_pos, hop=new_hop, occ=new_occ, add_cc=False, uc=self._uc)

    @_modifying
    def change_uc(self, uc, in_place=False):
        """
        Creates a new model with a different unit cell. The new unit cell must have the same volume as the previous one, i.e. the number of atoms per unit cell stays the same, and cannot change chirality.

        :param uc: The new unit cell, given w.r.t. to the old one. Lattice vectors are given as column vectors in a 3x3 matrix.

        :param in_place:    Determines whether the current model is modified (``in_place=True``) or a new model is returned, preserving the current one (``in_place=False``, default).
        :type in_place:     bool
        """
        uc = np.array(uc)
        if la.det(uc) != 1:
            raise ValueError('The determinant of uc is {0}, but should be 1'.format(la.det(uc)))
        if self._uc is not None:
            new_uc = np.dot(self._uc, uc)
        else:
            new_uc = None
        new_pos = [la.solve(uc, p) for p in self.pos]
        new_hop = [[i0, i1, np.array(la.solve(uc, G), dtype=int), t] for i0, i1, G, t in self._hop]

        return self._create_model(in_place, on_site=self._on_site, pos=new_pos, hop=new_hop, occ=self.occ, add_cc=False, uc=new_uc)

    @_modifying
    def em_field(self, scalar_pot, vec_pot, prefactor_scalar=1, prefactor_vec=7.596337572e-6, mode_scalar='relative', mode_vec='relative', in_place=False):
        r"""
        Creates a model including an electromagnetic field described by a scalar potential :math:`\Phi(\mathbf{r})` and a vector potential :math:`\mathbf{A}(\mathbf{r})` .

        :param scalar_pot:  A function returning the scalar potential given the position as a numpy ``array`` of length 3.
        :type scalar_pot:   function

        :param vec_pot: A function returning the vector potential (``list`` or ``numpy array`` of length 3) given the position as a numpy ``array`` of length 3.
        :type vec_pot:  function

        The units in which the two potentials are given can be determined by specifying a multiplicative prefactor. By default, the scalar potential is given in :math:`\frac{\text{energy}}{\text{electron}}` in the given energy units, and the scalar potential is given in :math:`\text{T} \cdot {\buildrel _{\circ} \over {\mathrm{A}}}`, assuming that the unit cell is also given in Angstrom.

        Given a ``prefactor_scalar`` :math:`p_s` and ``prefactor_vec`` :math:`p_v`, the on-site energies are modified by

        :math:`\epsilon_{\alpha, \mathbf{R}} = \epsilon_{\alpha, \mathbf{R}}^0 + p_s \Phi(\mathbf{R})`

        and the hopping terms are transformed by

        :math:`t_{\alpha^\prime , \alpha } (\mathbf{R}, \mathbf{R}^\prime) = t_{\alpha^\prime , \alpha }^0 (\mathbf{R}, \mathbf{R}^\prime) \times \exp{\left[ -i ~ p_v~(\mathbf{R}^\prime - \mathbf{R})\cdot(\mathbf{A}(\mathbf{R}^\prime ) - \mathbf{A}(\mathbf{R})) \right]}`

        :param prefactor_scalar:    Prefactor determining the unit of the scalar potential.
        :type prefactor_scalar:     float

        :param prefactor_vec:       Prefactor determining the unit of the vector potential.
        :type prefactor_vec:        float

        The positions :math:`\mathbf{r}` given to the potentials :math:`\Phi` and :math:`\mathbf{A}` can be either absolute or relative to the unit cell:

        :param mode_scalar: Determines whether the input for the ``scalar_pot`` function is given as an absolute position (``mode_scalar=='absolute'``) or relative to the unit cell (``mode_scalar=='relative'``).
        :type mode_scalar:  str

        :param mode_vec:    Determines whether the input for the ``vec_pot`` function is given as an absolute position (``mode_vec=='absolute'``) or relative to the unit cell (``mode_vec=='relative'``).
        :type mode_vec:     str

        Additional parameters:

        :param in_place:    Determines whether the current model is modified (``in_place=True``) or a new model is returned, preserving the current one (``in_place=False``, default).
        :type in_place:     bool
        """
        new_on_site = copy.deepcopy(self._on_site)
        if scalar_pot is not None:
            for i, p in enumerate(self.pos):
                if mode_scalar == 'relative':
                    new_on_site[i] += prefactor_scalar * scalar_pot(p)
                    #~ print('adding {1} to site {0}'.format(i, prefactor_scalar * scalar_pot(p)))
                elif mode_scalar == 'absolute':
                    new_on_site[i] += prefactor_scalar * scalar_pot(np.dot(self._uc, p))
                else:
                    raise ValueError('Unrecognized value for mode_scalar. Must be either "absolute" or "relative"')

        if vec_pot is not None:
            if self._uc is None:
                raise ValueError('Unit cell is not specified')
            new_hop = []
            for i0, i1, G, t in self._hop:
                p0 = self.pos[i0]
                p1 = self.pos[i0]
                r0 = np.dot(self._uc, p0)
                r1 = np.dot(self._uc, p1)
                if mode_vec == 'absolute':
                    # project into the home UC
                    A0 = vec_pot(np.dot(self._uc, p0 % 1))
                    A1 = vec_pot(np.dot(self._uc, p1 % 1))
                elif mode_vec == 'relative':
                    # project into the home UC
                    A0 = vec_pot(p0 % 1)
                    A1 = vec_pot(p1 % 1)
                else:
                    raise ValueError('Unrecognized value for mode_vec. Must be either "absolute" or "relative"')
                new_t = t * np.exp(-1j * prefactor_vec * np.dot(G + r1 - r0, A1 - A0))
                new_hop.append(i0, i1, G, new_t)
        else:
            new_hop = copy.deepcopy(self._hop)

        return self._create_model(in_place, on_site=new_on_site, pos=self.pos, hop=new_hop, occ=self.occ, add_cc=False, uc=self._uc)

#----------------HELPER FUNCTIONS FOR SUPERCELL-------------------------#
def _pos_to_idx(pos, dim):
    """index -> position"""
    for p, d in zip(pos, dim):
        if p >= d:
            raise IndexError('pos is out of bounds')
    return ((pos[0] * dim[1]) + pos[1]) * dim[2] + pos[2]

def _edge_detect_pos(pos, dim):
    """detect edges of the supercell"""
    for p, d in zip(pos, dim):
        if p >= d:
            raise IndexError('pos is out of bounds')
    edges = [[None] * 2 for i in range(3)]
    for i in range(3):
        edges[i][0] = (pos[i] == 0)
        edges[i][1] = (pos[i] == dim[i] - 1)
    return edges

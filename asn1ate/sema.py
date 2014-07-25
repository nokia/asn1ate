# Copyright (c) 2013, Schneider Electric Buildings AB
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#     * Redistributions of source code must retain the above copyright
#       notice, this list of conditions and the following disclaimer.
#     * Redistributions in binary form must reproduce the above copyright
#       notice, this list of conditions and the following disclaimer in the
#       documentation and/or other materials provided with the distribution.
#     * Neither the name of Schneider Electric Buildings AB nor the
#       names of contributors may be used to endorse or promote products
#       derived from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR
# ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

from asn1ate import parser


def build_semantic_model(parse_result):
    """ Build a semantic model of the ASN.1 definition
    from a syntax tree generated by asn1ate.parser.
    """
    root = []
    for token in parse_result:
        _assert_annotated_token(token)
        root.append(_create_sema_node(token))

    return root


def topological_sort(assignments):
    """ Algorithm adapted from:
    http://en.wikipedia.org/wiki/Topological_sorting.

    Use this in code generators to sort assignments in dependency order, IFF
    there are no circular dependencies.

    Assumes assignments is an iterable of items with two methods:
    - reference_name() -- returns the reference name of the assignment
    - references() -- returns an iterable of reference names
    upon which the assignment depends.
    """
    graph = dict((a.reference_name(), a.references()) for a in assignments)

    def has_predecessor(node):
        for predecessors in graph.values():
            if node in predecessors:
                return True

        return False

    # Build a topological order of reference names
    topological_order = []
    roots = [name for name in graph.keys()
             if not has_predecessor(name)]

    while roots:
        root = roots.pop()

        # Remove the current node from the graph
        # and collect all new roots (the nodes that
        # were previously only referenced from n)
        successors = graph.pop(root, set())
        roots.extend(successor for successor in successors
                     if not has_predecessor(successor))

        topological_order.insert(0, root)

    if graph:
        raise Exception('Can\'t sort cyclic references: %s' % graph)

    # Sort the actual assignments based on the topological order
    return sorted(assignments,
                  key=lambda a: topological_order.index(a.reference_name()))


def dependency_sort(assignments):
    """ We define a dependency sort as a Tarjan strongly-connected
    components resolution. Tarjan's algorithm happens to topologically
    sort as a by-product of finding strongly-connected components.

    Use this in code generators to sort assignments in dependency order, if
    there are circular dependencies. It is slower than ``topological_sort``.

    In the sema model, each node depends on types mentioned in its
    ``descendants``. The model is nominally a tree, except ``descendants``
    can contain node references forming a cycle.

    Returns a list of tuples, where each item represents a component
    in the graph. Ideally they're one-tuples, but if the graph has cycles
    items can have any number of elements constituting a cycle.

    This is nice, because the output is in perfect dependency order,
    except for the cycle components, where there is no order. They
    can be detected on the basis of their plurality and handled
    separately.
    """
    # Build reverse-lookup table from name -> node.
    assignments_by_name = {a.reference_name(): a for a in assignments}

    # Build the dependency graph.
    graph = {}
    for assignment in assignments:
        references = assignment.references()
        graph[assignment] = [assignments_by_name[r] for r in references
                             if r in assignments_by_name]

    # Now let Tarjan do its work! Adapted from here:
    # http://www.logarithmic.net/pfh-files/blog/01208083168/tarjan.py
    index_counter = [0]
    stack = []
    lowlinks = {}
    index = {}
    result = []

    def strongconnect(node):
        # Set the depth index for this node to the smallest unused index
        index[node] = index_counter[0]
        lowlinks[node] = index_counter[0]
        index_counter[0] += 1
        stack.append(node)

        # Consider successors of `node`
        successors = graph.get(node, [])
        for successor in successors:
            if successor not in lowlinks:
                # Successor has not yet been visited; recurse on it
                strongconnect(successor)
                lowlinks[node] = min(lowlinks[node], lowlinks[successor])
            elif successor in stack:
                # the successor is in the stack and hence in the current
                # strongly connected component (SCC)
                lowlinks[node] = min(lowlinks[node], index[successor])

        # If `node` is a root node, pop the stack and generate an SCC
        if lowlinks[node] == index[node]:
            connected_component = []

            while True:
                successor = stack.pop()
                connected_component.append(successor)
                if successor == node: break

            component = tuple(connected_component)
            result.append(component)

    for node in graph:
        if node not in lowlinks:
            strongconnect(node)

    return result


# Registered object identifier names
REGISTERED_OID_NAMES = {
    'ccitt': 0,
    'iso': 1,
    'joint-iso-ccitt': 2,
    # ccitt
    'recommendation': 0,
    'question': 1,
    'administration': 2,
    'network-operator': 3,
    # iso
    'standard': 0,
    'registration-authority': 1,
    'member-body': 2,
    'identified-organization': 3,
    # joint-iso-ccitt
    'country': 16,
    'registration-procedures': 17
}

"""
Sema nodes

Concepts in the ASN.1 specification are mirrored here as a simple object model.

Class and member names typically follow the ASN.1 terminology, but there are
some concepts captured which are not expressed in the spec.

Most notably, we build a dependency graph of all types and values in a module,
to allow code generators to build code in dependency order.

All nodes that somehow denote a referenced type or value, either definitions
(type and value assignments) or references (referenced types, referenced values,
etc) must have a method called ``reference_name``.
"""

class SemaNode(object):
    """ Base class for all sema nodes. """

    def children(self):
        """ Return a list of all contained sema nodes.

        This implementation finds all member variables of type
        SemaNode. It also expands list members, to transparently
        handle the case where a node holds a list of other
        sema nodes.
        """
        # Collect all SemaNode members.
        members = list(vars(self).values())
        children = [m for m in members if isinstance(m, SemaNode)]

        # Expand SemaNodes out of list members, but do not recurse
        # through lists of lists.
        list_members = [m for m  in members if isinstance(m, list)]
        for m in list_members:
            children.extend(n for n in m if isinstance(n, SemaNode))

        return children

    def descendants(self):
        """ Return a list of all recursively contained sema nodes.
        """
        descendants = []
        for child in self.children():
            descendants.append(child)
            descendants.extend(child.descendants())

        return descendants


class Module(SemaNode):
    def __init__(self, elements):
        self._user_types = {}

        module_reference, _, _, _, _, module_body, _ = elements
        exports, imports, assignments = module_body.elements

        self.name = module_reference.elements[0]
        self.assignments = [_create_sema_node(token) for token in assignments.elements]

    def user_types(self):
        if not self._user_types:
            # Index all type assignments by name
            type_assignments = [a for a in self.assignments if isinstance(a, TypeAssignment)]
            for user_defined in type_assignments:
                self._user_types[user_defined.type_name] = user_defined.type_decl

        return self._user_types

    def resolve_type_decl(self, type_decl):
        """ Recursively resolve user-defined types to their
        built-in declaration.
        """
        user_types = self.user_types()

        if isinstance(type_decl, ReferencedType):
            return self.resolve_type_decl(user_types[type_decl.type_name])
        else:
            return type_decl


    def __str__(self):
        return '%s DEFINITIONS ::=\n' % self.name \
            + 'BEGIN\n' \
            + '\n'.join(map(str, self.assignments)) + '\n' \
            + 'END\n'

    __repr__ = __str__


class Assignment(SemaNode):
    def references(self):
        """ Return a set of all reference names (both values and types) that
        this assignment depends on.

        This happens to coincide with all contained SemaNodes as exposed by
        ``descendants`` with a ``reference_name`` method.
        """
        return set(d.reference_name() for d in self.descendants()
                   if hasattr(d, 'reference_name'))


class TypeAssignment(Assignment):
    def __init__(self, elements):
        assert(len(elements) == 3)
        type_name, _, type_decl = elements
        self.type_name = type_name
        self.type_decl = _create_sema_node(type_decl)

    def reference_name(self):
        return self.type_name

    def __str__(self):
        return '%s ::= %s' % (self.type_name, self.type_decl)

    __repr__ = __str__


class ValueAssignment(Assignment):
    def __init__(self, elements):
        value_name, type_name, _, value = elements
        self.value_name = value_name
        self.type_decl = _create_sema_node(type_name)
        self.value = _maybe_create_sema_node(value)

    def reference_name(self):
        return self.value_name

    def __str__(self):
        return '%s %s ::= %s' % (self.value_name, self.type_decl, self.value)

    __repr__ = __str__


class ConstructedType(SemaNode):
    """ Base type for SEQUENCE, SET and CHOICE. """
    def __init__(self, elements):
        type_name, component_tokens = elements
        self.type_name = type_name
        self.components = [_create_sema_node(token) for token in component_tokens]

    def __str__(self):
        component_type_list = ', '.join(map(str, self.components))
        return '%s { %s }' % (self.type_name, component_type_list)

    __repr__ = __str__


class ChoiceType(ConstructedType):
    def __init__(self, elements):
        super(ChoiceType, self).__init__(elements)


class SequenceType(ConstructedType):
    def __init__(self, elements):
        super(SequenceType, self).__init__(elements)


class SetType(ConstructedType):
    def __init__(self, elements):
        super(SetType, self).__init__(elements)


class CollectionType(SemaNode):
    """ Base type for SET OF and SEQUENCE OF. """
    def __init__(self, kind, elements):
        self.kind = kind
        self.type_name = self.kind + ' OF'

        if elements[0].ty == 'Type':
            self.size_constraint = None
            self.type_decl = _create_sema_node(elements[0])
        elif elements[0].ty == 'SizeConstraint':
            self.size_constraint = _create_sema_node(elements[0])
            self.type_decl = _create_sema_node(elements[1])
        else:
            assert False, 'Unknown form of %s OF declaration: %s' % (self.kind, elements)

    def __str__(self):
        if self.size_constraint:
            return '%s %s OF %s' % (self.kind, self.size_constraint, self.type_decl)
        else:
            return '%s OF %s' % (self.kind, self.type_decl)

    __repr__ = __str__


class SequenceOfType(CollectionType):
    def __init__(self, elements):
        super(SequenceOfType, self).__init__('SEQUENCE', elements)


class SetOfType(CollectionType):
    def __init__(self, elements):
        super(SetOfType, self).__init__('SET', elements)


class TaggedType(SemaNode):
    def __init__(self, elements):
        self.class_name = None
        self.class_number = None
        self.implicit = False

        tag_token = elements[0]
        if type(elements[1]) is parser.AnnotatedToken:
            type_token = elements[1]
        else:
            self.implicit = elements[1] == 'IMPLICIT'
            type_token = elements[2]

        for tag_element in tag_token.elements:
            if tag_element.ty == 'TagClassNumber':
                self.class_number = tag_element.elements[0]
            elif tag_element.ty == 'TagClass':
                self.class_name = tag_element.elements[0]
            else:
                assert False, 'Unknown tag element: %s' % tag_element

        self.type_decl = _create_sema_node(type_token)

    @property
    def type_name(self):
        return self.type_decl.type_name

    def __str__(self):
        class_spec = []
        if self.class_name:
            class_spec.append(self.class_name)
        class_spec.append(self.class_number)

        result = '[%s] ' % ' '.join(class_spec)
        if self.implicit:
            result += 'IMPLICIT '

        result += str(self.type_decl)

        return result

    __repr__ = __str__


class SimpleType(SemaNode):
    def __init__(self, elements):
        self.constraint = None
        self.type_name = elements[0]
        if len(elements) > 1 and elements[1].ty == 'Constraint':
            self.constraint = Constraint(elements[1].elements)

    def __str__(self):
        if self.constraint is None:
            return self.type_name

        return '%s %s' % (self.type_name, self.constraint)

    __repr__ = __str__


class ReferencedType(SemaNode):
    def __init__(self, elements):
        # TODO: Module references are not resolved at the moment,
        # and I'm not sure how to handle them.
        if len(elements) > 1 and elements[0].ty == 'ModuleReference':
            self.module_reference = elements[0].elements[0]
            self.type_name = elements[1]
        else:
            self.module_reference = None
            self.type_name = elements[0]

    def reference_name(self):
        return self.type_name

    def __str__(self):
        return self.type_name

    __repr__ = __str__


class ReferencedValue(SemaNode):
    def __init__(self, elements):
        self.name = elements[0]

    def reference_name(self):
        return self.name

    def __str__(self):
        return self.name

    __repr__ = __str__


class Constraint(SemaNode):
    def __init__(self, elements):
        min_value, max_value = elements

        self.min_value = _maybe_create_sema_node(min_value)
        self.max_value = _maybe_create_sema_node(max_value)

    def __str__(self):
        return '(%s..%s)' % (self.min_value, self.max_value)

    __repr__ = __str__


class SizeConstraint(Constraint):
    """ Size constraints have the same form as any value range constraints."""
    def __str__(self):
        return 'SIZE(%s..%s)' % (self.min_value, self.max_value)

    __repr__ = __str__


class ComponentType(SemaNode):
    def __init__(self, elements):
        self.identifier = None
        self.type_decl = None
        self.default_value = None
        self.optional = False
        self.components_of_type = None

        def crack_named_type(token):
            named_type = NamedType(token)
            self.identifier = named_type.identifier
            self.type_decl = named_type.type_decl

        first_token = elements[0]
        if first_token.ty == 'NamedType':
            crack_named_type(first_token.elements)
        elif first_token.ty == 'ComponentTypeOptional':
            crack_named_type(first_token.elements[0].elements)
            self.optional = True
        elif first_token.ty == 'ComponentTypeDefault':
            crack_named_type(first_token.elements[0].elements)
            self.default_value = _maybe_create_sema_node(first_token.elements[1])
        elif first_token.ty == 'ComponentTypeComponentsOf':
            self.components_of_type = _create_sema_node(first_token.elements[0])
        else:
            assert False, 'Unknown component type %s' % first_token

    def __str__(self):
        if self.components_of_type:
            return 'COMPONENTS OF %s' % self.components_of_type

        result = '%s %s' % (self.identifier, self.type_decl)
        if self.optional:
            result += ' OPTIONAL'
        elif self.default_value is not None:
            result += ' DEFAULT %s' % self.default_value

        return result

    __repr__ = __str__


class NamedType(SemaNode):
    def __init__(self, elements):
        first_token = elements[0]
        if first_token.ty == 'Type':
            # EXT: unnamed member
            type_token = first_token
            self.identifier = _get_next_unnamed()
        elif first_token.ty == 'Identifier':
            # an identifier
            self.identifier = first_token.elements[0]
            type_token = elements[1]

        self.type_decl = _create_sema_node(type_token)

    def __str__(self):
        return '%s %s' % (self.identifier, self.type_decl)

    __repr__ = __str__


class ValueListType(SemaNode):
    def __init__(self, elements):
        self.type_name = elements[0]
        if len(elements) > 1:
            self.named_values = [_create_sema_node(token) for token in elements[1]]
            for idx, n in enumerate(self.named_values):
                if isinstance(n, NamedValue) and n.value is None:
                    if idx == 0:
                        n.value = str(0)
                    else:
                        n.value = str(int(self.named_values[idx-1].value) + 1)
        else:
            self.named_values = None

    def __str__(self):
        if self.named_values:
            named_value_list = ', '.join(map(str, self.named_values))
            return '%s { %s }' % (self.type_name, named_value_list)
        else:
            return '%s' % self.type_name

    __repr__ = __str__


class BitStringType(SemaNode):
    def __init__(self, elements):
        self.type_name = elements[0]
        if len(elements) > 1:
            self.named_bits = [_create_sema_node(token) for token in elements[1]]
        else:
            self.named_bits = None

    def __str__(self):
        if self.named_bits:
            named_bit_list = ', '.join(map(str, self.named_bits))
            return '%s { %s }' % (self.type_name, named_bit_list)
        else:
            return '%s' % self.type_name

    __repr__ = __str__


class NamedValue(SemaNode):
    def __init__(self, elements):
        if len(elements) == 1:
            identifier_token = elements[0]
            self.identifier = identifier_token
            self.value = None
        else:
            identifier_token, value_token = elements
            self.identifier = identifier_token.elements[0]
            self.value = value_token.elements[0]

    def __str__(self):
        return '%s (%s)' % (self.identifier, self.value)

    __repr__ = __str__


class ExtensionMarker(SemaNode):
    def __init__(self, elements):
        pass

    def __str__(self):
        return '...'

    __repr__ = __str__


class NameForm(SemaNode):
    def __init__(self, elements):
        self.name = elements[0]

    def reference_name(self):
        return self.name

    def __str__(self):
        return self.name

    __repr__ = __str__


class NumberForm(SemaNode):
    def __init__(self, elements):
        self.value = elements[0]

    def __str__(self):
        return str(self.value)

    __repr__ = __str__


class NameAndNumberForm(SemaNode):
    def __init__(self, elements):
        self.name = _create_sema_node(elements[0])
        self.number = _create_sema_node(elements[1])

    def __str__(self):
        return '%s(%s)' % (self.name, self.number)

    __repr__ = __str__


class ObjectIdentifierValue(SemaNode):
    def __init__(self, elements):
        self.components = [_create_sema_node(c) for c in elements]

    def __str__(self):
        return '{' + ' '.join(str(x) for x in self.components) + '}'

    __repr__ = __str__


class BinaryStringValue(SemaNode):
    def __init__(self, elements):
        self.value = elements[0]

    def __str__(self):
        return '\'%s\'B' % self.value

    __repr__ = __str__


class HexStringValue(SemaNode):
    def __init__(self, elements):
        self.value = elements[0]

    def __str__(self):
        return '\'%s\'H' % self.value

    __repr__ = __str__


def _maybe_create_sema_node(token):
    if isinstance(token, parser.AnnotatedToken):
        return _create_sema_node(token)
    else:
        return token


def _create_sema_node(token):
    _assert_annotated_token(token)

    if token.ty == 'ModuleDefinition':
        return Module(token.elements)
    elif token.ty == 'TypeAssignment':
        return TypeAssignment(token.elements)
    elif token.ty == 'ValueAssignment':
        return ValueAssignment(token.elements)
    elif token.ty == 'ComponentType':
        return ComponentType(token.elements)
    elif token.ty == 'NamedType':
        return NamedType(token.elements)
    elif token.ty == 'ValueListType':
        return ValueListType(token.elements)
    elif token.ty == 'BitStringType':
        return BitStringType(token.elements)
    elif token.ty == 'NamedValue':
        return NamedValue(token.elements)
    elif token.ty == 'Type':
        # Type tokens have a more specific type category
        # embedded as their first element
        return _create_sema_node(token.elements[0])
    elif token.ty == 'SimpleType':
        return SimpleType(token.elements)
    elif token.ty == 'ReferencedType':
        return ReferencedType(token.elements)
    elif token.ty == 'ReferencedValue':
        return ReferencedValue(token.elements)
    elif token.ty == 'TaggedType':
        return TaggedType(token.elements)
    elif token.ty == 'SequenceType':
        return SequenceType(token.elements)
    elif token.ty == 'ChoiceType':
        return ChoiceType(token.elements)
    elif token.ty == 'SetType':
        return SetType(token.elements)
    elif token.ty == 'SequenceOfType':
        return SequenceOfType(token.elements)
    elif token.ty == 'SetOfType':
        return SetOfType(token.elements)
    elif token.ty == 'ExtensionMarker':
        return ExtensionMarker(token.elements)
    elif token.ty == 'SizeConstraint':
        return SizeConstraint(token.elements)
    elif token.ty == 'ObjectIdentifierValue':
        return ObjectIdentifierValue(token.elements)
    elif token.ty == 'NameForm':
        return NameForm(token.elements)
    elif token.ty == 'NumberForm':
        return NumberForm(token.elements)
    elif token.ty == 'NameAndNumberForm':
        return NameAndNumberForm(token.elements)
    elif token.ty == 'BinaryStringValue':
        return BinaryStringValue(token.elements)
    elif token.ty == 'HexStringValue':
        return HexStringValue(token.elements)

    raise Exception('Unknown token type: %s' % token.ty)


def _assert_annotated_token(obj):
    assert(type(obj) is parser.AnnotatedToken)


# HACK: Generate unique names for unnamed members
_unnamed_counter = 0
def _get_next_unnamed():
    global _unnamed_counter
    _unnamed_counter += 1
    return 'unnamed%d' % _unnamed_counter

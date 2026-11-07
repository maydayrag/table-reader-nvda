"""以 2026.3 原始行列導覽方法驗證橋接；系統 IAccessible 與語音仍需實機測試。"""
import ast
from dataclasses import dataclass
from pathlib import Path
import types
import unittest
import test_addon as base

root = Path(__file__).resolve().parent
nvda = root.parent / 'nvda-2026.3-source/source'

@dataclass
class Offsets:
    startOffset: int
    endOffset: int
class ControlField(dict): pass
class FieldCommand:
    def __init__(self, command, field):self.command=command;self.field=field
textInfos = base.module('textInfos', TextInfo=object, ControlField=ControlField, FieldCommand=FieldCommand, UNIT_CHARACTER='character', POSITION_ALL='all')
base.module('textInfos.offsets', Offsets=Offsets)
config = types.SimpleNamespace(conf={'documentFormatting':{'includeLayoutTables':False}})

# 從使用者指定的正式版原始碼取出原方法執行，而非另寫一套假表格移動邏輯。
source = ast.parse((nvda/'documentBase.py').read_text(encoding='utf-8'))
names={'_Axis','_Movement','_TableCell'}
selected=[node for node in source.body if isinstance(node,ast.ClassDef) and node.name in names]
methods={'_maybeGetLayoutTableIds','_getTableCellCoords','_getTableDimensions','_getNearestTableCell','_getFirstOrLastTableCell'}
original=next(node for node in source.body if isinstance(node,ast.ClassDef) and node.name=='DocumentWithTableNavigation')
nativeClass=ast.ClassDef(name='DocumentWithTableNavigation',bases=[],keywords=[],body=[node for node in original.body if isinstance(node,ast.FunctionDef) and node.name in methods],decorator_list=[])
selected.append(nativeClass)
tree=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)]+selected,type_ignores=[])
namespace={'dataclass':dataclass,'Enum':__import__('enum').Enum,'textInfos':textInfos,'config':config,'controlTypes':types.SimpleNamespace(Role=base.Role)}
exec(compile(ast.fix_missing_locations(tree),str(nvda/'documentBase.py'),'exec'),namespace)
docModule=base.module('documentBase', **{key:namespace[key] for key in names|{'DocumentWithTableNavigation'}})
Native=docModule.DocumentWithTableNavigation
Native._missingTableCellSearchLimit=3

class QuickItem:
    def __init__(self,kind,doc,info):self.textInfo=info
    def __lt__(self,other):return self.textInfo._startOffset<other.textInfo._startOffset
mergeAst=next(node for node in ast.parse((nvda/'browseMode.py').read_text(encoding='utf-8')).body if isinstance(node,ast.FunctionDef) and node.name=='mergeQuickNavItemIterators')
mergeNamespace={};exec(compile(ast.Module(body=[mergeAst],type_ignores=[]),'mergeQuickNav','exec'),mergeNamespace)
base.module('browseMode', TextInfoQuickNavItem=QuickItem, mergeQuickNavItemIterators=mergeNamespace['mergeQuickNavItemIterators'])
from tableReader.bridge import Bridge
from tableReader.model import Node, Table

class Info:
    encoding='utf_16_le'
    def __init__(self,doc,pos):
        self.obj=doc
        if pos=='all':self._startOffset=0;self._endOffset=len(doc.text)
        elif isinstance(pos,Info):self._startOffset=pos._startOffset;self._endOffset=pos._endOffset
        elif isinstance(pos,Offsets):self._startOffset=pos.startOffset;self._endOffset=pos.endOffset
        else:self._startOffset,self._endOffset=pos.range
    @property
    def text(self):return self.obj.text[self._startOffset:self._endOffset]
    @property
    def isCollapsed(self):return self._startOffset==self._endOffset
    def copy(self):return type(self)(self.obj,self)
    def expand(self,unit):self._endOffset=self._startOffset+1
    def getTextWithFields(self,formatConfig=None):return [self.text]
    def _getLineOffsets(self,offset):return (0,len(self.obj.text))
    def _getParagraphOffsets(self,offset):return (0,len(self.obj.text))
    def _setSelectionOffsets(self,start,end):self.obj.selectionOffsets=(start,end)

class Document(Native):
    TextInfo=Info
    def __init__(self):self.text='N...A B C D...Z';self.VBufHandle=1;self.updated=0;self.unloaded=0
    def makeTextInfo(self,pos):return self.TextInfo(self,pos)
    def _iterNodesByType(self,nodeType,direction='next',pos=None):
        if nodeType != 'table':return iter(())
        start=pos._startOffset if pos else (-1 if direction=='next' else 999)
        positions=[0,14] if direction=='next' else [14,0]
        return iter(QuickItem('table',self,self.makeTextInfo(Offsets(p,p+1))) for p in positions if (p>start if direction=='next' else p<start))
    def _getTableCellAt(self,tableID,startPos,row,column):
        if tableID=='native':return self.makeTextInfo(Offsets(0,1))
        raise LookupError
    def _getNearestTableCell(self,startPos,cell,movement,axis):return self._getTableCellAt('native',startPos,1,1)
    def _handleUpdate(self):self.updated+=1
    def unloadBuffer(self):self.VBufHandle=None;self.unloaded+=1

def candidate():
    nodes=[]
    for row in range(2):
        nodes.append([Node(source=types.SimpleNamespace(range=(4+(row*2+col)*2,5+(row*2+col)*2))) for col in range(2)])
    return Table([['A','B'],['C','D']],True,True,nodes)

class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.doc=Document();self.bridge=Bridge(self.doc,lambda:False)
        self.assertEqual(self.bridge.mapTables([candidate()]),1);self.bridge.install()
    def tearDown(self):self.bridge.detach()
    def test_reading_units_follow_each_cell_and_gaps(self):
        info=self.doc.makeTextInfo(Offsets(4,4))
        for method in (info._getLineOffsets,info._getParagraphOffsets):
            self.assertEqual(method(0),(0,4))
            for offset in range(4,11):
                self.assertEqual(method(offset),(offset,offset+1))
            self.assertEqual(method(11),(11,len(self.doc.text)))
        self.bridge.detach()
        self.assertEqual(info._getLineOffsets(4),(0,len(self.doc.text)))

    def test_preserves_wrapping_inside_long_cell(self):
        cell=self.bridge.tables[0].cells[(1,1)]
        cell.start=4;cell.end=7
        self.assertEqual(self.bridge.readingBounds(5,(5,6)),(5,6))

    def test_native_line_move_progresses_in_both_directions(self):
        # 直接執行正式版 move，確認格間空白不會讓翻行卡住或倒退。
        tree=ast.parse((nvda/'textInfos/offsets.py').read_text(encoding='utf-8'))
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='OffsetsTextInfo')
        method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='move')
        scope={}
        exec(compile(ast.Module(body=[method],type_ignores=[]),'nativeMove','exec'),scope)
        info=self.doc.makeTextInfo(Offsets(0,0))
        info.collapse=lambda:setattr(info,'_endOffset',info._startOffset)
        info._getStoryLength=lambda:len(self.doc.text)
        info.allowMoveToUnitOffsetPastEnd=lambda unit:False
        info._getUnitOffsets=lambda unit,offset:info._getLineOffsets(offset)
        positions=[0]
        while scope['move'](info,'line',1):positions.append(info._startOffset)
        self.assertEqual(positions,[0,4,5,6,7,8,9,10,11])
        backwards=[info._startOffset]
        while scope['move'](info,'line',-1):backwards.append(info._startOffset)
        self.assertEqual(backwards,list(reversed(positions)))

    def test_braille_notification_after_selection_write(self):
        calls=[]
        base.module('braille',handler=types.SimpleNamespace(handleCaretMove=lambda obj,**kw:calls.append((obj.selectionOffsets,kw))))
        info=self.doc.makeTextInfo(Offsets(6,6))
        info._setSelectionOffsets(6,6)
        self.assertEqual(calls,[((6,6),{'shouldAutoTether':False})])
        self.bridge.detach()
        info._setSelectionOffsets(8,8)
        self.assertEqual(len(calls),1)
    def test_next_previous_merge(self):
        self.assertEqual([item.textInfo._startOffset for item in self.doc._iterNodesByType('table')],[0,4,14])
        self.assertEqual([item.textInfo._startOffset for item in self.doc._iterNodesByType('table','previous',self.doc.makeTextInfo(Offsets(15,15)))],[14,4,0])
        self.assertEqual([item.textInfo._startOffset for item in self.doc._iterNodesByType('table','next',self.doc.makeTextInfo(Offsets(4,4)))],[14])
    def test_native_coordinates_and_movement(self):
        pos=self.doc.makeTextInfo(Offsets(4,4));cell=self.doc._getTableCellCoords(pos)
        self.assertEqual((cell.row,cell.col),(1,1))
        info=self.doc._getNearestTableCell(pos,cell,docModule._Movement.NEXT,docModule._Axis.COLUMN)
        self.assertEqual(info.text,'B')
        info=self.doc._getNearestTableCell(pos,cell,docModule._Movement.NEXT,docModule._Axis.ROW)
        self.assertEqual(info.text,'C')
        info=self.doc._getFirstOrLastTableCell(pos,cell,docModule._Movement.LAST,docModule._Axis.ROW)
        self.assertEqual(info.text,'C');self.assertEqual(self.doc._getTableDimensions(pos),(2,2))
    def test_edge_and_native_fallback(self):
        pos=self.doc.makeTextInfo(Offsets(10,10));cell=self.doc._getTableCellCoords(pos)
        with self.assertRaises(LookupError):self.doc._getNearestTableCell(pos,cell,docModule._Movement.NEXT,docModule._Axis.COLUMN)
        self.assertEqual(self.doc._getTableCellAt('native',pos,1,1).text,'N')
    def test_fields_preserve_text_and_headers(self):
        fields=self.doc.makeTextInfo(Offsets(0,len(self.doc.text))).getTextWithFields()
        self.assertEqual(''.join(item for item in fields if isinstance(item,str)),self.doc.text)
        cellfields=[item.field for item in fields if isinstance(item,FieldCommand) and item.command=='controlStart' and 'table-columnnumber' in item.field]
        self.assertEqual(len(cellfields),4);self.assertEqual(cellfields[2]['table-columnheadertext'],'A')
        depth=0
        for item in fields:
            if isinstance(item,FieldCommand):depth+=1 if item.command=='controlStart' else -1;self.assertGreaterEqual(depth,0)
        self.assertEqual(depth,0)

    def test_clickable_filtered_only_in_mapped_cells_without_mutation(self):
        self.bridge.detach()
        field=ControlField({'role':base.Role.UNKNOWN,'states':{base.State.CLICKABLE,base.State.PROTECTED},'name':'action'})
        class ClickableInfo(Info):
            def getTextWithFields(self,formatConfig=None):
                return [FieldCommand('controlStart',field),self.text,FieldCommand('controlEnd',None)]
        self.doc.TextInfo=ClickableInfo
        self.bridge=Bridge(self.doc,lambda:False)
        self.assertEqual(self.bridge.mapTables([candidate()]),1)
        self.bridge.install()
        inside=self.doc.makeTextInfo(Offsets(4,5)).getTextWithFields()
        filtered=next(c.field for c in inside if isinstance(c,FieldCommand) and c.field and c.field.get('name')=='action')
        self.assertEqual(filtered['states'],{base.State.PROTECTED})
        self.assertEqual(filtered['role'],field['role'])
        self.assertIn(base.State.CLICKABLE,field['states'])
        outside=self.doc.makeTextInfo(Offsets(0,1)).getTextWithFields()
        self.assertIs(outside[0].field,field)
        self.bridge.detach()
        self.assertIn(base.State.CLICKABLE,self.doc.makeTextInfo(Offsets(4,5)).getTextWithFields()[0].field['states'])
    def test_unchanged_update_preserves_table_after_leaving(self):
        self.doc.makeTextInfo(Offsets(11,14)).getTextWithFields()
        self.doc._handleUpdate()
        self.assertEqual(self.doc.updated,1);self.assertTrue(self.bridge.active)
        items=list(self.doc._iterNodesByType('table','previous',self.doc.makeTextInfo(Offsets(13,13))))
        self.assertEqual([item.textInfo._startOffset for item in items],[4,0])
        self.assertEqual(self.doc.makeTextInfo(Offsets(6,6))._getLineOffsets(6),(6,7))

    def test_update_outside_table_preserves_mapping(self):
        self.doc.text=self.doc.text[:-1]+'Q'
        self.doc._handleUpdate()
        self.assertTrue(self.bridge.active);self.assertEqual(self.doc.updated,1)

    def test_changed_update_detaches_and_preserves_original(self):
        self.doc.text=self.doc.text.replace('C','Q')
        self.doc._handleUpdate();self.assertEqual(self.doc.updated,1);self.assertFalse(self.bridge.active)
        self.assertIs(self.doc.TextInfo,Info);self.assertNotIn('_iterNodesByType',self.doc.__dict__)

    def test_shifted_table_update_detaches(self):
        self.doc.text='prefix'+self.doc.text
        self.doc._handleUpdate()
        self.assertFalse(self.bridge.active);self.assertEqual(self.doc.updated,1)

    def test_replaced_buffer_update_detaches(self):
        self.doc.VBufHandle=2
        self.doc._handleUpdate()
        self.assertFalse(self.bridge.active);self.assertEqual(self.doc.updated,1)
    def test_same_length_mutation_detaches(self):
        self.doc.text=self.doc.text.replace('C','Q');self.doc.makeTextInfo(Offsets(4,5)).getTextWithFields()
        self.assertFalse(self.bridge.active)
    def test_old_textinfo_after_detach(self):
        info=self.doc.makeTextInfo(Offsets(4,5));self.bridge.detach();self.assertEqual(info.getTextWithFields(),['A'])
    def test_unload(self):self.doc.unloadBuffer();self.assertEqual(self.doc.unloaded,1);self.assertFalse(self.bridge.active)
    def test_mapping_mismatch_and_overlap(self):
        self.bridge.detach();broken=candidate();broken.rows[0][0]='wrong'
        self.assertEqual(self.bridge.mapTables([broken]),0)
        broken=candidate();broken.nodes[0][1].source.range=(4,5);broken.rows[0][1]='A'
        self.assertEqual(self.bridge.mapTables([broken]),0)
    def test_restricted_state(self):
        self.bridge.restricted=lambda:True
        self.assertFalse(self.bridge.valid());self.assertIs(self.doc.TextInfo,Info)
    def test_container_missing_uses_verified_child(self):
        self.bridge.detach();item=candidate()
        item.nodes[0][0]=Node(source=object(),children=[Node(text='A',source=types.SimpleNamespace(range=(4,5)))])
        self.assertEqual(self.bridge.mapTables([item]),1)
    def test_broad_container_uses_verified_child(self):
        self.bridge.detach();item=candidate()
        item.nodes[0][0]=Node(source=types.SimpleNamespace(range=(4,11)),children=[Node(text='A',source=types.SimpleNamespace(range=(4,5)))])
        self.assertEqual(self.bridge.mapTables([item]),1)
    def test_multi_child_requires_exact_combined_text(self):
        self.bridge.detach()
        item=Node(source=object(),children=[Node(text='A',source=types.SimpleNamespace(range=(4,5))),Node(text='B',source=types.SimpleNamespace(range=(6,7)))])
        self.assertEqual(self.bridge.resolveCell(item,'A B').text,'A B')
        self.doc.text=self.doc.text[:5]+'X'+self.doc.text[6:]
        with self.assertRaises(Exception) as error:self.bridge.resolveCell(item,'A B')
        self.assertEqual(str(error.exception),'CHILD_COMBINED_MISMATCH')
    def test_diagnostics_contain_no_source_text(self):
        self.bridge.detach();item=candidate();item.rows[0][0]='PRIVATE-KEY-EXAMPLE'
        self.assertEqual(self.bridge.mapTables([item]),0)
        self.assertIn('SEQUENCE_NOT_FOUND',self.bridge.diagnosticText())
        self.assertNotIn('PRIVATE',self.bridge.diagnosticText())
        self.assertEqual(self.bridge.diagnostics[0][:3],(1,0,0))
    def test_diagnostics_native_fields(self):
        self.bridge.detach()
        class NativeInfo(Info):
            def getTextWithFields(self,formatConfig=None):return [FieldCommand('controlStart',ControlField({'table-id':'original'})),self.text,FieldCommand('controlEnd',None)]
        self.doc.TextInfo=NativeInfo
        bridge=Bridge(self.doc,lambda:False)
        self.assertEqual(bridge.mapTables([candidate()]),0)
        self.assertEqual(bridge.diagnostics[0][-1],'NATIVE_TABLE')
    def test_whole_sequence_without_object_ranges(self):
        self.bridge.detach();item=candidate()
        for row in item.nodes:
            for node in row:node.source=object()
        self.assertEqual(self.bridge.mapTables([item]),1)
        self.assertEqual(self.bridge.sequenceMatches,1)
        self.bridge.install()
        cell=self.doc._getTableCellCoords(self.doc.makeTextInfo(Offsets(4,4)))
        self.assertEqual(self.doc._getNearestTableCell(None,cell,docModule._Movement.NEXT,docModule._Axis.ROW).text,'C')
    def test_duplicate_whole_sequence_rejected(self):
        self.bridge.detach();self.doc.text+=' A B C D';item=candidate()
        for row in item.nodes:
            for node in row:node.source=object()
        self.assertEqual(self.bridge.mapTables([item]),0)
        self.assertEqual(self.bridge.diagnostics[0][-1],'SEQUENCE_AMBIGUOUS')

    def test_sequence_empty_cells_preserve_column_numbers(self):
        self.bridge.detach()
        rows=[['A','','B'],['C','','D']]
        item=Table(rows,True,True,[[Node(source=object()) for value in row] for row in rows])
        self.assertEqual(self.bridge.mapTables([item]),1)
        self.bridge.install()
        table=self.bridge.tables[0]
        self.assertEqual(set(table.cells),{(1,1),(1,3),(2,1),(2,3)})
        cell=self.doc._getTableCellCoords(self.doc.makeTextInfo(Offsets(4,4)))
        nextInfo=self.doc._getNearestTableCell(None,cell,docModule._Movement.NEXT,docModule._Axis.COLUMN)
        self.assertEqual(nextInfo.text,'B')
        self.assertEqual(self.doc._getTableCellCoords(nextInfo).col,3)

if __name__=='__main__':unittest.main()

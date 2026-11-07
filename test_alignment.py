"""只用人工文字檢查完整序列與 UTF-16 邊界，不含私人頁面資料。"""
import unittest
import test_addon
from tableReader.alignment import align, AlignmentFailure

class AlignmentTests(unittest.TestCase):
    def test_whitespace_and_repeated_cell(self):
        self.assertEqual(align('before A\nB A\tD after',['A','B','A','D']),[(7,8),(9,10),(11,12),(13,14)])
    def test_utf16(self):
        text='😀 名稱 值 甲 🐈'
        ranges=align(text,['名稱','值','甲','🐈'])
        data=text.encode('utf-16-le')
        self.assertEqual([data[a*2:b*2].decode('utf-16-le') for a,b in ranges],['名稱','值','甲','🐈'])
        self.assertEqual(ranges[0][0],3)
    def test_duplicate_rejected(self):
        with self.assertRaises(AlignmentFailure) as failure:align('ABCD ABCD',['A','B','C','D'])
        self.assertEqual(str(failure.exception),'SEQUENCE_AMBIGUOUS')
    def test_changed_character_rejected(self):
        with self.assertRaises(AlignmentFailure):align('A B X D',['A','B','C','D'])
    def test_extra_token_rejected(self):
        with self.assertRaises(AlignmentFailure):align('A B X C D',['A','B','C','D'])
    def test_empty_cell_rejected(self):
        with self.assertRaises(AlignmentFailure):align('A B C',['A','B','','C'])
    def test_blank_cells_keep_coordinates_without_invented_ranges(self):
        self.assertEqual(align('A B C D',['','A',' ','B','C','D','']),[None,(0,1),None,(2,3),(4,5),(6,7),None])
    def test_blank_cells_do_not_allow_hidden_extra_text(self):
        with self.assertRaises(AlignmentFailure):align('A X B C D',['A','','B','C','D'])
    def test_insufficient_has_specific_code(self):
        with self.assertRaises(AlignmentFailure) as failure:align('A B C',['A','','B','C'])
        self.assertEqual(str(failure.exception),'SEQUENCE_INSUFFICIENT')
    def test_size_limit(self):
        with self.assertRaises(AlignmentFailure):align('x'*500001,['A','B','C','D'])

if __name__=='__main__':unittest.main()

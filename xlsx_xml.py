"""Leitor OOXML .xlsx em streaming, usando apenas a biblioteca padrão.
Preserva identificadores textuais e converte números inteiros em notação científica sem perda extra.
"""
import io,posixpath,re,zipfile
from decimal import Decimal,InvalidOperation
from xml.etree import ElementTree as ET
M='{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
R='{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'

def colnum(ref):
    m=re.match(r'[A-Z]+',ref or '')
    n=0
    for a in m.group(0) if m else '':n=n*26+ord(a)-64
    return n-1

def numeric_identifier(value):
    if value is None:return ''
    v=str(value).strip()
    if not v:return ''
    try:
        d=Decimal(v)
        if d==d.to_integral_value():return str(int(d))
    except (InvalidOperation,ValueError):pass
    return v

def read_sheets(blob):
    """Yields (sheet_name, iterable of (excel_row_number, {zero_based_col: string})).
    Caller must consume iterator before advancing sheets.
    """
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        strings=[]
        if 'xl/sharedStrings.xml' in z.namelist():
            with z.open('xl/sharedStrings.xml') as h:
                for _,el in ET.iterparse(h, events=('end',)):
                    if el.tag==M+'si':
                        strings.append(''.join(t.text or '' for t in el.iter(M+'t')))
                        el.clear()
        book=ET.fromstring(z.read('xl/workbook.xml'))
        relroot=ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        rels={rel.get('Id'):rel.get('Target') for rel in relroot}
        for sheet in book.findall('.//'+M+'sheet'):
            target=rels.get(sheet.get(R+'id'),'')
            path=target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join('xl',target))
            if path not in z.namelist():continue
            def rows_for(path=path):
                with z.open(path) as h:
                    for _,el in ET.iterparse(h,events=('end',)):
                        if el.tag!=M+'row':continue
                        row={}
                        for c in el.findall(M+'c'):
                            i=colnum(c.get('r'))
                            typ=c.get('t');ve=c.find(M+'v')
                            if typ=='inlineStr':v=''.join(t.text or '' for t in c.iter(M+'t'))
                            elif ve is None:v=''
                            else:
                                v=ve.text or ''
                                if typ=='s':v=strings[int(v)]
                                elif typ in ('n',None):v=numeric_identifier(v)
                            if str(v).strip():row[i]=str(v).strip()
                        num=int(el.get('r','0'))
                        el.clear()
                        if row:yield num,row
            yield sheet.get('name','Planilha'), rows_for()

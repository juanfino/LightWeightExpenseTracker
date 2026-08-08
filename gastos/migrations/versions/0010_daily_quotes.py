"""Add global curated daily quotes.

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0010"
down_revision: Union[str, Sequence[str], None] = "0009"
branch_labels = None
depends_on = None


_SEED = """El que guarda, siempre tiene.|Dicho popular|es-AR|1
De a poquito se llena el jarrito.|Dicho popular|es-AR|1
Guardá pan para mayo.|Refrán|es-AR|1
Cuidado con los gastos pequeños: una pequeña gotera hunde un gran barco.|Benjamin Franklin|es|1
No ahorres lo que te queda después de gastar; gastá lo que te queda después de ahorrar.|Atribuido a Warren Buffett|es|0
Ahorrar es la brecha entre tu ego y tus ingresos.|Morgan Housel|es|1
La riqueza es lo que no ves.|Morgan Housel|es|1
Cuidá los centavos, que los pesos se cuidan solos.|Dicho popular|es|1
No es pobre el que tiene poco, sino el que mucho desea.|Séneca|es|1
Si querés hacer rico a alguien, no le agregues dinero: quitale deseos.|Atribuido a Epicuro|es|0
La riqueza no consiste en tener muchas posesiones, sino en tener pocas necesidades.|Atribuido a Epicteto|es|0
Uno es rico en proporción a la cantidad de cosas que puede permitirse dejar en paz.|Henry David Thoreau|es|1
El precio de algo es la cantidad de vida que entregás a cambio.|Henry David Thoreau|es|1
Lo barato sale caro.|Refrán|es|1
Quien compra lo superfluo, pronto venderá lo necesario.|Benjamin Franklin|es|1
Un centavo ahorrado es un centavo ganado.|Atribuido a Benjamin Franklin|es|0
Quien guarda, halla.|Refrán|es|1
El ahorro es la base de la fortuna.|Dicho popular|es|1
El precio es lo que pagás; el valor es lo que recibís.|Warren Buffett|es|1
En la inversión, obtenés lo que no pagás.|John C. Bogle|es|1
El que mucho abarca, poco aprieta.|Refrán|es|1
Más vale pájaro en mano que cien volando.|Refrán|es|1
Cuentas claras conservan amistades.|Refrán|es|1
Lo que no se mide, no se mejora.|Atribuido a Peter Drucker|es|0
Un presupuesto le dice a tu plata adónde ir, en lugar de preguntarte adónde se fue.|Atribuido a John Maxwell|es|0
La primera regla del interés compuesto: nunca lo interrumpas innecesariamente.|Charlie Munger|es|1
El tiempo es el amigo del buen negocio y el enemigo del mediocre.|Warren Buffett|es|1
El principal enemigo del inversor es, probablemente, él mismo.|Benjamin Graham|es|1
Regla número uno: nunca pierdas dinero. Regla número dos: no olvides la regla número uno.|Warren Buffett|es|1
Hombre prevenido vale por dos.|Refrán|es|1
Más vale prevenir que curar.|Refrán|es|1
No se puede repicar y andar en la procesión.|Refrán|es|1
Somos lo que hacemos repetidamente; la excelencia no es un acto, sino un hábito.|Will Durant, resumiendo a Aristóteles|es|1
Un viaje de mil millas comienza con un solo paso.|Lao Tse|es|1
No importa lo lento que vayas, mientras no te detengas.|Atribuido a Confucio|es|0
Ya no discutas cómo debe ser una buena persona: sé una.|Marco Aurelio|es|1
Nuestra vida es lo que nuestros pensamientos hacen de ella.|Marco Aurelio|es|1
Mientras esperamos vivir, la vida pasa.|Séneca|es|1
No hay viento favorable para el que no sabe adónde va.|Séneca|es|1
La felicidad depende de nosotros mismos.|Aristóteles|es|1
Conocete a vos mismo.|Inscripción del templo de Delfos|es|1
No por mucho madrugar amanece más temprano.|Refrán|es|1
Al que madruga, Dios lo ayuda.|Refrán|es|1
Despacio, que estoy apurado.|Dicho popular|es-AR|1
Lo importante rara vez es urgente, y lo urgente rara vez es importante.|Atribuido a Dwight D. Eisenhower|es|0
Poco a poco hila la vieja el copo.|Refrán|es|1
Camarón que se duerme, se lo lleva la corriente.|Dicho popular|es-AR|1
A cada chancho le llega su San Martín.|Dicho popular|es-AR|1
El tiempo es dinero.|Benjamin Franklin|es|1
No malgastes el tiempo: es la materia de la que está hecha la vida.|Benjamin Franklin|es|1
La primera riqueza es la salud.|Ralph Waldo Emerson|es|1
La economía es el arte de sacarle el máximo provecho a la vida.|George Bernard Shaw|es|1
No dejes para mañana lo que puedas hacer hoy.|Refrán|es|1
Roma no se construyó en un día.|Refrán|es|1
El que espera, desespera.|Dicho popular|es|1
Zapatero, a tus zapatos.|Refrán|es|1
Agua que no has de beber, dejala correr.|Refrán|es|1
El que no arriesga, no gana.|Dicho popular|es|1
No hay atajo sin trabajo.|Refrán|es|1
Nadie es tan pobre como el que solo tiene dinero.|Dicho popular|es|1
Lo que es prudencia en la conducta de una familia difícilmente sea insensatez en la de un gran reino.|Adam Smith|es|1
Ingreso anual veinte libras, gasto diecinueve: felicidad. Ingreso veinte, gasto veintiuna: miseria.|Charles Dickens|es|1
Donde comen dos, comen tres.|Refrán|es|1
En casa de herrero, cuchillo de palo.|Refrán|es|1
Quien tiene un oficio, tiene un beneficio.|Refrán|es|1
El que paga sus deudas, hace caudal.|Refrán|es|1
Casa chica, corazón grande.|Dicho popular|es-AR|1
La ropa sucia se lava en casa.|Refrán|es|1
Cuenta y razón sustentan amistad.|Refrán|es|1
Quien no economiza en la abundancia, padece en la escasez.|Dicho popular|es|1
No existe tal cosa como un almuerzo gratis.|Milton Friedman|es|1
La inflación es el único impuesto que puede imponerse sin legislación.|Milton Friedman|es|1
A largo plazo, todos muertos.|John Maynard Keynes|es|1
Es difícil hacer predicciones, sobre todo acerca del futuro.|Atribuido a Niels Bohr|es|0
La economía es enormemente útil como fuente de empleo para economistas.|John Kenneth Galbraith|es|1
Un economista es un experto que mañana sabrá explicar por qué lo que predijo ayer no pasó hoy.|Atribuido a Laurence J. Peter|es|0
Cuando el río suena, agua trae.|Refrán|es|1
A caballo regalado no se le miran los dientes.|Refrán|es|1
Del dicho al hecho hay mucho trecho.|Refrán|es|1
Cada oveja con su pareja.|Refrán|es|1"""


def _tag_for(index: int) -> str:
    if index <= 18:
        return "ahorro"
    if index <= 32:
        return "consumo"
    if index <= 48:
        return "habitos"
    if index <= 60:
        return "tiempo"
    if index <= 70:
        return "familia"
    return "economia"


def upgrade() -> None:
    quotes = op.create_table(
        "quotes",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("author", sa.Text(), nullable=False),
        sa.Column("language", sa.Text(), nullable=False, server_default="es"),
        sa.Column("tag", sa.Text(), nullable=True),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    rows = []
    for index, line in enumerate(_SEED.splitlines(), start=1):
        text, author, language, verified = line.split("|")
        rows.append({
            "text": text, "author": author, "language": language,
            "tag": _tag_for(index), "verified": verified == "1", "active": True,
        })
    op.bulk_insert(quotes, rows)
    op.execute("GRANT SELECT ON quotes TO gastos_app, gastos_superadmin")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO gastos_superadmin")


def downgrade() -> None:
    op.drop_table("quotes")

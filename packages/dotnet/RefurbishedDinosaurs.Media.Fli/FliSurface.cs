using System.Buffers.Binary;

namespace RefurbishedDinosaurs.Media.Fli;

/// <summary>Persistent indexed pixels and RGB palette for sequential AF11 FLI records.</summary>
/// <remarks>Supports COLOR_64, LC line deltas, BLACK, BRUN and COPY. Type 14 is ignored;
/// other chunk types fail explicitly. Discard a surface after a decoding exception.</remarks>
public sealed class FliSurface
{
    private readonly byte[] _pixels;
    private readonly byte[] _palette = new byte[768];
    /// <summary>Pixel width.</summary>
    public int Width { get; }
    /// <summary>Pixel height.</summary>
    public int Height { get; }
    /// <summary>Top-down palette indices, valid until the next decoded record.</summary>
    public ReadOnlyMemory<byte> Indices => _pixels;
    /// <summary>256 RGB triples, valid until the next decoded record.</summary>
    public ReadOnlyMemory<byte> Palette => _palette;

    /// <summary>Creates a black, zero-palette surface.</summary>
    public FliSurface(int width, int height)
    {
        if (width is <= 0 or > 4096 || height is <= 0 or > 4096)
            throw new ArgumentOutOfRangeException(nameof(width));
        Width = width;
        Height = height;
        _pixels = new byte[checked(width * height)];
    }

    /// <summary>Applies one complete frame record. Decode all records in ascending order, including unchanged records.</summary>
    public void DecodeFrame(ReadOnlySpan<byte> record)
    {
        if (record.Length < 16 || record.Length > FliMovieStream.MaximumFrameBytes
            || BinaryPrimitives.ReadUInt32LittleEndian(record) != record.Length
            || BinaryPrimitives.ReadUInt16LittleEndian(record[4..]) != 0xF1FA)
            throw new InvalidDataException("FLI frame header is invalid.");
        int count = BinaryPrimitives.ReadUInt16LittleEndian(record[6..]);
        int position = 16;
        for (int i = 0; i < count; i++)
        {
            if (record.Length - position < 6) throw new InvalidDataException("FLI chunk header is truncated.");
            uint length = BinaryPrimitives.ReadUInt32LittleEndian(record[position..]);
            int type = BinaryPrimitives.ReadUInt16LittleEndian(record[(position + 4)..]);
            if (length < 6 || length > record.Length - position) throw new InvalidDataException("FLI chunk extent is invalid.");
            var data = record.Slice(position + 6, (int)length - 6);
            switch (type)
            {
                case 11: DecodePalette(data); break;
                case 12: DecodeDelta(data); break;
                case 13: Array.Clear(_pixels); break;
                case 14: break;
                case 15: DecodeRuns(data); break;
                case 16:
                    if (data.Length < _pixels.Length) throw new InvalidDataException("FLI COPY is truncated.");
                    data[.._pixels.Length].CopyTo(_pixels);
                    break;
                default: throw new NotSupportedException($"FLI chunk type {type} is unsupported.");
            }
            position += (int)length;
        }
        // FLI records may contain alignment bytes after their declared chunks.
    }

    private void DecodePalette(ReadOnlySpan<byte> data)
    {
        var reader = new PacketReader(data);
        int packets = reader.Word();
        int entry = 0;
        for (int i = 0; i < packets; i++)
        {
            entry += reader.Byte();
            int count = reader.Byte();
            if (count == 0) count = 256;
            if (entry > 256 - count) throw new InvalidDataException("FLI palette packet leaves the palette.");
            for (int j = 0; j < count * 3; j++)
            {
                int value = reader.Byte();
                if (value > 63) throw new InvalidDataException("FLI palette component is not six-bit.");
                _palette[entry * 3 + j] = (byte)((value << 2) | (value >> 4));
            }
            entry += count;
        }
    }

    private void DecodeDelta(ReadOnlySpan<byte> data)
    {
        var reader = new PacketReader(data);
        int first = reader.Word();
        int lines = reader.Word();
        if (first > Height - lines) throw new InvalidDataException("FLI delta lines leave the surface.");
        for (int y = first; y < first + lines; y++)
        {
            int packets = reader.Byte();
            int x = 0;
            for (int p = 0; p < packets; p++)
            {
                x += reader.Byte();
                int count = (sbyte)reader.Byte();
                WriteRun(ref reader, y, ref x, Math.Abs(count), repeat: count < 0);
            }
        }
    }

    private void DecodeRuns(ReadOnlySpan<byte> data)
    {
        var reader = new PacketReader(data);
        for (int y = 0; y < Height; y++)
        {
            reader.Byte(); // Packet count is advisory; the row width terminates BRUN decoding.
            int x = 0;
            while (x < Width)
            {
                int count = (sbyte)reader.Byte();
                if (count == 0) throw new InvalidDataException("FLI BRUN packet makes no progress.");
                WriteRun(ref reader, y, ref x, Math.Abs(count), repeat: count > 0);
            }
        }
    }

    private void WriteRun(ref PacketReader reader, int y, ref int x, int count, bool repeat)
    {
        if (x < 0 || count > Width - x) throw new InvalidDataException("FLI pixel run leaves its row.");
        var target = _pixels.AsSpan(y * Width + x, count);
        if (repeat && count != 0) target.Fill(reader.Byte());
        else for (int i = 0; i < count; i++) target[i] = reader.Byte();
        x += count;
    }

    private ref struct PacketReader(ReadOnlySpan<byte> source)
    {
        private readonly ReadOnlySpan<byte> _source = source;
        private int _position;
        public byte Byte()
        {
            if (_position >= _source.Length) throw new InvalidDataException("FLI packet is truncated.");
            return _source[_position++];
        }
        public int Word() => Byte() | (Byte() << 8);
    }
}

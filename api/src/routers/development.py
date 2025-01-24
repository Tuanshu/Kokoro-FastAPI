from typing import List

import numpy as np
import torch
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from loguru import logger

from ..services.audio import AudioService
from ..services.text_processing import phonemize, tokenize
from ..services.tts_service import TTSService
from ..structures.text_schemas import (
    GenerateFromPhonemesRequest,
    PhonemeRequest,
    PhonemeResponse,
)

router = APIRouter(tags=["text processing"])


async def get_tts_service() -> TTSService:
    """Dependency to get TTSService instance"""
    return await TTSService.create()  # Create service with properly initialized managers


@router.post("/text/phonemize", response_model=PhonemeResponse, tags=["deprecated"])
@router.post("/dev/phonemize", response_model=PhonemeResponse)
async def phonemize_text(request: PhonemeRequest) -> PhonemeResponse:
    """Convert text to phonemes and tokens

    Args:
        request: Request containing text and language
        tts_service: Injected TTSService instance

    Returns:
        Phonemes and token IDs
    """
    try:
        if not request.text:
            raise ValueError("Text cannot be empty")

        # Get phonemes
        phonemes, _ = phonemize(request.text, request.language)  # Unpack tuple
        if not phonemes:
            raise ValueError("Failed to generate phonemes")

        # Get tokens (without adding start/end tokens to match process_text behavior)
        tokens = tokenize(phonemes)
        return PhonemeResponse(phonemes=phonemes, tokens=tokens)
    except ValueError as e:
        logger.error(f"Error in phoneme generation: {str(e)}")
        raise HTTPException(
            status_code=500, detail={"error": "Server error", "message": str(e)}
        )
    except Exception as e:
        logger.error(f"Error in phoneme generation: {str(e)}")
        raise HTTPException(
            status_code=500, detail={"error": "Server error", "message": str(e)}
        )


@router.post("/text/generate_from_phonemes", tags=["deprecated"])
@router.post("/dev/generate_from_phonemes")
async def generate_from_phonemes(
    request: GenerateFromPhonemesRequest,
    tts_service: TTSService = Depends(get_tts_service),
) -> Response:
    """Generate audio directly from phonemes

    Args:
        request: Request containing phonemes and generation parameters
        tts_service: Injected TTSService instance

    Returns:
        WAV audio bytes
    """
    # Validate phonemes first
    if not request.phonemes:
        raise HTTPException(
            status_code=400,
            detail={"error": "Invalid request", "message": "Phonemes cannot be empty"},
        )

    try:
        # Validate voice exists
        available_voices = await tts_service.list_voices()
        if request.voice not in available_voices:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "Invalid request",
                    "message": f"Voice not found: {request.voice}",
                },
            )

        # Handle both single string and list of chunks
        phoneme_chunks = [request.phonemes] if isinstance(request.phonemes, str) else request.phonemes
        audio_chunks = []

        # Load voice tensor first since we'll need it for all chunks
        voice_tensor = await tts_service._voice_manager.load_voice(
            request.voice,
            device=tts_service.model_manager.get_backend().device
        )

        try:
            # Process each chunk
            for chunk in phoneme_chunks:
                # Convert chunk to tokens
                tokens = tokenize(chunk)
                tokens = [0] + tokens + [0]  # Add start/end tokens

                # Validate chunk length
                if len(tokens) > 510:  # 510 to leave room for start/end tokens
                    raise ValueError(
                        f"Chunk too long ({len(tokens)} tokens). Each chunk must be under 510 tokens."
                    )

                # Generate audio for chunk
                chunk_audio, _ = await tts_service.model_manager.generate(  # Unpack tuple
                    tokens,
                    voice_tensor,
                    speed=request.speed
                )
                if chunk_audio is not None:
                    audio_chunks.append(chunk_audio)

            # Combine chunks if needed
            if len(audio_chunks) > 1:
                audio = np.concatenate(audio_chunks)
            elif len(audio_chunks) == 1:
                audio = audio_chunks[0]
            else:
                raise ValueError("No audio chunks were generated")

        finally:
            # Clean up voice tensor
            del voice_tensor
            torch.cuda.empty_cache()

        # Convert to WAV bytes
        wav_bytes = AudioService.convert_audio(
            audio, 24000, "wav", is_first_chunk=True, is_last_chunk=True, stream=False,
        )

        return Response(
            content=wav_bytes,
            media_type="audio/wav",
            headers={
                "Content-Disposition": "attachment; filename=speech.wav",
                "Cache-Control": "no-cache",
            },
        )

    except ValueError as e:
        logger.error(f"Invalid request: {str(e)}")
        raise HTTPException(
            status_code=400, detail={"error": "Invalid request", "message": str(e)}
        )
    except Exception as e:
        logger.error(f"Error generating audio: {str(e)}")
        raise HTTPException(
            status_code=500, detail={"error": "Server error", "message": str(e)}
        )
